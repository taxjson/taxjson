"""Pre-release security review fixes (round 018). Synthetic data, fake
account ids, no network.

- M1/L5: a file replaced by `opening --force`, `find-missing-history
  --write-purchases --force`, `init --force` / `format --write` keeps
  its previous version at the next free <name>.bak / .bakN; an earlier
  backup is never overwritten and a symlink planted at a .bak name is
  never written through (lib/safe_write.backup_copy).
- M2/I4: an opening line is written only from a symbol, currency and
  lot date of a safe shape; the positions readers refuse control
  characters / whitespace in symbol and currency cells.
- L2: Questrade's description key is linear on long whitespace runs.
- L3: the `# From:` header of an opening file carries no control
  character of the report's file name.
- L4: the cannot-detect message masks an id in the file name.
- L5: a draft's account name is checked before it names a folder.
- I1: a channel tag with a trailing newline is not a release tag.
- I3: `init` never writes through a dangling symlink.
"""
import json
import os
import tempfile
import time
import unittest
from pathlib import Path

from tax_rules.dual import cli, projects_both

from taxjson.lib import positions_reports as P
from taxjson.lib.safe_write import backup_copy

from test_positions_reports import _IB_STATEMENT, _RBC_HOLD, _write


class TestBackupCopy(unittest.TestCase):
    def test_next_free_name_and_planted_symlink_untouched(self):
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            victim = d / "victim.txt"
            victim.write_text("keep me\n")
            f = d / "x.tt"
            f.write_text("one\n")
            (d / "x.tt.bak").symlink_to(victim)
            b1 = backup_copy(f)
            self.assertEqual(b1.name, "x.tt.bak1")
            self.assertEqual(b1.read_text(), "one\n")
            self.assertEqual(victim.read_text(), "keep me\n")
            # Same content again: the existing backup is reused.
            self.assertEqual(backup_copy(f), b1)
            f.write_text("two\n")
            b2 = backup_copy(f)
            self.assertEqual(b2.name, "x.tt.bak2")
            self.assertEqual(b1.read_text(), "one\n")
            # A dangling link at a .bak name is taken, not followed.
            (d / "y.tt").write_text("y\n")
            (d / "y.tt.bak").symlink_to(d / "nowhere")
            self.assertEqual(backup_copy(d / "y.tt").name, "y.tt.bak1")
            self.assertFalse((d / "nowhere").exists())


def _opening_project(td):
    root = Path(td) / "p"
    (root / "inputs" / "margin").mkdir(parents=True)
    (root / "taxjson.toml").write_text(
        '[settings]\nyear = 2025\ncountry = "canada"\n'
        'base_currency = "CAD"\nsource_currencies = []\n'
        'option_grant_timing_since = 2025\n'
        '[accounts.margin]\ntype = "taxable"\n')
    return root


def _holdings(path, sym="SAMPB.TO", cur="CAD", acquired=None, cost=200.0):
    h = {"symbol": sym, "quantity": 20, "currency": cur,
         "total_cost": cost}
    if acquired:
        h["acquired"] = acquired
    body = ['[meta]\naccount = "55500001"\nas_of = "2025-01-31"',  # pii-ok
            "[[holding]]"] + [f"{k} = {json.dumps(v)}" for k, v in h.items()]
    path.write_text("\n".join(body) + "\n")
    return path


class TestOpeningBackup(unittest.TestCase):
    def test_force_never_writes_through_a_planted_bak_symlink(self):
        with tempfile.TemporaryDirectory() as td:
            root = _opening_project(td)
            victim = Path(td) / "victim.txt"
            victim.write_text("keep me\n")
            rep = _holdings(Path(td) / "pos.toml")
            r = cli(root, "opening", "margin", str(rep))
            self.assertEqual(r.returncode, 0, r.stderr)
            out = root / "inputs" / "margin" / "opening_2025-01-31.tt"
            first = out.read_text()
            (out.parent / (out.name + ".bak")).symlink_to(victim)
            _holdings(rep, cost=300.0)
            r = cli(root, "opening", "margin", str(rep), "--force")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(victim.read_text(), "keep me\n")
            self.assertEqual(
                (out.parent / (out.name + ".bak1")).read_text(), first)
            self.assertIn("300.00", out.read_text())
            # A third --force keeps the second version too.
            _holdings(rep, cost=400.0)
            r = cli(root, "opening", "margin", str(rep), "--force")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("300.00", (out.parent / (out.name + ".bak2"))
                          .read_text())
            self.assertEqual((out.parent / (out.name + ".bak1"))
                             .read_text(), first)


class TestWritePurchasesAccountName(unittest.TestCase):
    def _book(self, account):
        from test_basis_drafts import SINGLE, _parsed
        return {"work/margin_base.json": json.dumps(
                    {"transactions": _parsed(SINGLE, account=account)}),
                "inputs/margin/ib.csv": ""}

    def test_a_path_like_or_unconfigured_account_is_refused(self):
        for acct in ("../../escape", "other"):
            with tempfile.TemporaryDirectory() as td:
                root = projects_both(td, files=self._book(acct))["usa"]
                r = cli(root, "find-missing-history", "--write-purchases")
                self.assertNotEqual(r.returncode, 0, (acct, r.stdout))
                self.assertIn("account", r.stderr)
                self.assertEqual(
                    [p for p in Path(td).rglob("purchases_draft*")], [])

    def test_force_keeps_every_earlier_draft(self):
        from taxjson.lib.missing_history import DRAFT_NAME
        with tempfile.TemporaryDirectory() as td:
            root = projects_both(td, files=self._book("margin"))["usa"]
            draft = root / "inputs" / "margin" / DRAFT_NAME
            victim = Path(td) / "victim.txt"
            victim.write_text("keep me\n")
            r = cli(root, "find-missing-history", "--write-purchases")
            self.assertEqual(r.returncode, 0, r.stderr)
            draft.write_text(draft.read_text() + "# edit 1\n")
            (draft.parent / (DRAFT_NAME + ".bak")).symlink_to(victim)
            r = cli(root, "find-missing-history", "--write-purchases",
                    "--force")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(victim.read_text(), "keep me\n")
            self.assertIn("# edit 1", (draft.parent / (DRAFT_NAME + ".bak1"))
                          .read_text())
            draft.write_text(draft.read_text() + "# edit 2\n")
            r = cli(root, "find-missing-history", "--write-purchases",
                    "--force")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("# edit 1", (draft.parent / (DRAFT_NAME + ".bak1"))
                          .read_text())
            self.assertIn("# edit 2", (draft.parent / (DRAFT_NAME + ".bak2"))
                          .read_text())
            self.assertIn(DRAFT_NAME + ".bak2", r.stderr)


if __name__ == "__main__":
    unittest.main()
