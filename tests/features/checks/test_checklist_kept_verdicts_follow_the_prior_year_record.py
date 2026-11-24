"""Backlog TJ-5089 (the same defect as TJ-4974's open item 10): a kept
checklist verdict goes stale when the lock [settings] prior_year_record
names changes, moves or goes.

The handoff step reads that lock (the prior year's carry-forward), but
the checklist fingerprint held only the setting's text (the planning
digest) and this project's own filed/: rewriting ../2024/filed/2024.json,
restoring it or deleting it left every kept verdict current. The lock,
resolved, and its content's hash (or `absent`) are now an entry of the
fingerprint (lib/checklist `PRIOR_RECORD_KEY`), so each change makes the
kept verdicts stale, "the inputs changed (prior_year_record lock)".
Synthetic projects only (tests/_support/filing_checklist_run_project)."""
import copy
import os
import tempfile
import unittest
from datetime import date
from pathlib import Path

from taxjson.lib import checklist as cl

from _support.filing_checklist_run_project import CFG, FakeSub, _project

PRIOR = "../2024/filed/2024.json"
WHY = "the inputs changed (prior_year_record lock)"


class KeptVerdictsFollowThePriorYearRecord(unittest.TestCase):

    def setUp(self):
        t = tempfile.TemporaryDirectory()
        self.addCleanup(t.cleanup)
        self.top = Path(t.name)
        self.root = self.top / "2025"
        self.root.mkdir()
        _project(self.root)
        self.lock = self.top / "2024" / "filed" / "2024.json"
        self.lock.parent.mkdir(parents=True)
        self.lock.write_text('{"year": 2024, "carry": 1}\n', encoding="utf-8")

    def _ctx(self, prior=PRIOR):
        cfg = copy.deepcopy(CFG)
        if prior is not None:
            cfg["settings"]["prior_year_record"] = prior
            toml = self.root / "taxjson.toml"
            text = toml.read_text(encoding="utf-8")
            if "prior_year_record" not in text:
                toml.write_text(text.replace(
                    "[settings]\n", f'[settings]\nprior_year_record = "{prior}"\n', 1),
                    encoding="utf-8")
        return cl.Ctx(root=self.root, cfg=cfg, year=2025, today=date(2026, 3, 1),
                      run_sub=FakeSub({}))

    def _kept(self, ctx):
        cl.save_results(ctx, [cl.Result("handoff", "done", "carried forward once")])

    def _stale(self, ctx):
        return cl.load_results(ctx)[1].stale

    def test_a_rewritten_lock_makes_the_kept_verdicts_stale(self):
        # The entry's repro, its first step: the fingerprint was the same.
        ctx = self._ctx()
        self._kept(ctx)
        self.lock.write_text('{"year": 2024, "carry": 2}\n', encoding="utf-8")
        self.assertEqual(self._stale(ctx), [WHY])

    def test_a_deleted_lock_makes_them_stale_and_so_does_its_return(self):
        ctx = self._ctx()
        self._kept(ctx)
        self.lock.unlink()
        self.assertEqual(self._stale(ctx), [WHY])
        self.assertTrue(cl.checklist_fingerprint(ctx)[cl.PRIOR_RECORD_KEY]
                        .endswith("=absent"))
        self._kept(ctx)                 # a verdict made with no lock there
        self.lock.write_text('{"year": 2024, "carry": 1}\n', encoding="utf-8")
        self.assertEqual(self._stale(ctx), [WHY])

    def test_the_lock_is_read_resolved(self):
        # ../2024 a link, pointed at another folder holding the same bytes.
        moved = self.top / "2024-a"
        (self.top / "2024").rename(moved)
        os.symlink("2024-a", self.top / "2024")
        ctx = self._ctx()
        self._kept(ctx)
        other = self.top / "2024-b" / "filed"
        other.mkdir(parents=True)
        (other / "2024.json").write_bytes((moved / "filed" / "2024.json").read_bytes())
        (self.top / "2024").unlink()
        os.symlink("2024-b", self.top / "2024")
        self.assertEqual(self._stale(ctx), [WHY])

    def test_an_unchanged_lock_leaves_them_current(self):
        # Control.
        ctx = self._ctx()
        self._kept(ctx)
        self.assertEqual(self._stale(ctx), [])

    def test_with_no_prior_year_record_a_lock_beside_is_no_dependency(self):
        # Control: the key unset, the fingerprint has no such entry.
        ctx = self._ctx(prior=None)
        self._kept(ctx)
        self.assertNotIn(getattr(cl, "PRIOR_RECORD_KEY", "prior_year_record lock"),
                         cl.checklist_fingerprint(ctx))
        self.lock.write_text('{"year": 2024, "carry": 2}\n', encoding="utf-8")
        self.assertEqual(self._stale(ctx), [])


if __name__ == "__main__":
    unittest.main()
