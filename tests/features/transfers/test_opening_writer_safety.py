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

Formerly: test_fix_prerelease_018
"""
import json
import tempfile
import unittest
from pathlib import Path

from tax_rules.dual import cli


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
    """Formerly: test_fix_prerelease_018.TestOpeningBackup"""
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


class TestOpeningHeaderName(unittest.TestCase):
    """Formerly: test_fix_prerelease_018.TestOpeningHeaderName"""
    def test_control_characters_in_the_file_name_never_reach_the_file(self):
        with tempfile.TemporaryDirectory() as td:
            root = _opening_project(td)
            rep = _holdings(Path(td) /
                            "pos\nOPENING 2025-01-01 EVIL 1 CAD 1\r.toml")
            r = cli(root, "opening", "margin", str(rep))
            self.assertEqual(r.returncode, 0, r.stderr)
            out = root / "inputs" / "margin" / "opening_2025-01-31.tt"
            body = [ln for ln in out.read_text().splitlines()
                    if ln and not ln.startswith("#")]
            self.assertEqual(body,
                             ["OPENING 2025-01-31 SAMPB.TO 20 CAD 200.00"])
            self.assertNotIn("\r", out.read_text())


if __name__ == "__main__":
    unittest.main()
