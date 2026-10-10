"""GitHub issues #26, #27, #29 (synthetic data only).

- #26: project_layout.set_key_text replaces (or comments out) the WHOLE
  value of a key — a multi-line array or string included; redact,
  align and migrate --to-years write a taxjson.toml that reads.
- #27: `new-year` knows a quoted (`[accounts."margin-main"]`) or
  hyphenated (`[accounts.margin-main]`) account table.
- #29: cra_slips._read_capped's one deadline covers the reading AND the
  process's exit.
"""
import subprocess
import sys
import time
import unittest
from pathlib import Path

from _style import CapturedWidth
from test_fix_multi_year import multi, single, tjs

from taxjson.lib import project_layout as PL
from taxjson.lib.tomlcompat import tomllib

_WIDTH = CapturedWidth()

IB = "U5550001"                                     # pii-ok
BROKER = "55500001"                                 # pii-ok
QUERY = "555123"                                    # pii-ok


def setUpModule():
    _WIDTH.start()


def tearDownModule():
    _WIDTH.stop()


def _year(extra_account: str = "", tail: str = "") -> Path:
    """A shared-exports project's 2024 folder whose margin account
    carries `extra_account` lines."""
    top = multi("canada", years=(2024,))
    y = top / "2024"
    t = (y / "taxjson.toml").read_text().replace(
        '[accounts.margin]\ntype = "taxable"\n',
        '[accounts.margin]\ntype = "taxable"\n' + extra_account)
    (y / "taxjson.toml").write_text(t + tail)
    return y


def _all_text(folder: Path) -> str:
    return "\n".join(p.read_text(errors="replace") for p in
                     sorted(folder.rglob("*")) if p.is_file())


_MULTI = """\
[settings]
year = 2024  # the tax year
note = \"\"\"
a line with ] and # and key = 1
\"\"\"
lit = '''
x = ]
'''

[accounts."margin-main"]
type = "taxable"
holdings = [
  "holdings/a.toml",  # the first
  "holdings/b]#.toml",
]
# a comment that stays

[accounts.tfsa]
type = "sheltered"
"""


class TestSetKeyText(unittest.TestCase):
    """#26."""

    def test_multiline_array_replaced(self):
        out = PL.set_key_text(_MULTI, ("accounts", "margin-main", "holdings"),
                              ["holdings/c.toml"])
        doc = tomllib.loads(out)
        self.assertEqual(doc["accounts"]["margin-main"]["holdings"],
                         ["holdings/c.toml"])
        self.assertEqual(doc["accounts"]["tfsa"]["type"], "sheltered")
        self.assertIn("# a comment that stays", out)
        self.assertNotIn("b]#.toml", out)

    def test_multiline_array_removed(self):
        out = PL.set_key_text(_MULTI, "accounts.margin-main.holdings", None)
        doc = tomllib.loads(out)
        self.assertNotIn("holdings", doc["accounts"]["margin-main"])
        self.assertIn('#   "holdings/b]#.toml",', out)
        self.assertEqual(doc["settings"]["note"],
                         "a line with ] and # and key = 1\n")

    def test_multiline_strings(self):
        out = PL.set_key_text(_MULTI, "settings.note", "short")
        doc = tomllib.loads(out)
        self.assertEqual(doc["settings"]["note"], "short")
        self.assertEqual(doc["settings"]["lit"], "x = ]\n")
        out = PL.set_key_text(out, "settings.lit", None)
        doc = tomllib.loads(out)
        self.assertNotIn("lit", doc["settings"])
        self.assertEqual(doc["settings"]["year"], 2024)
        # A key that only looks set inside a string is not one.
        out = PL.set_key_text(_MULTI, "settings.key", 2)
        self.assertEqual(tomllib.loads(out)["settings"]["key"], 2)

    def test_new_key_in_a_quoted_table(self):
        out = PL.set_key_text(_MULTI, "accounts.margin-main.plan", "x")
        doc = tomllib.loads(out)
        self.assertEqual(doc["accounts"]["margin-main"]["plan"], "x")
        out = PL.set_key_text(_MULTI, ("accounts", "new.one", "type"),
                              "taxable")
        self.assertEqual(tomllib.loads(out)["accounts"]["new.one"]["type"],
                         "taxable")

    def test_redact_multiline_holdings(self):
        y = _year('holdings = [\n  "holdings/demo.toml",\n]\n')
        (y / "holdings").mkdir()
        (y / "holdings" / "demo.toml").write_text(
            '[meta]\nas_of = "2024-12-31"\n\n[[holding]]\n'
            'symbol = "QZQ.TO"\nquantity = 1\n')
        r = tjs("-C", str(y), "redact")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        doc = tomllib.loads((y / "inputs_redact" / "taxjson.toml")
                            .read_text())
        self.assertEqual(doc["accounts"]["margin"]["holdings"],
                         ["holdings/demo.toml"])

    def test_align_multiline_value(self):
        top = multi("canada", years=(2024, 2025))
        for y, v in (("2024", '[\n  "USD",\n  "EUR",\n]'),
                     ("2025", '[\n  "USD",\n]')):
            p = top / y / "taxjson.toml"
            p.write_text(p.read_text().replace(
                'source_currencies = ["USD"]', f"source_currencies = {v}"))
        r = tjs("-C", str(top / "2025"), "align", "--from", "2024",
                "--write", "--all")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        doc = tomllib.loads((top / "2025" / "taxjson.toml").read_text())
        self.assertEqual(doc["settings"]["source_currencies"],
                         ["USD", "EUR"])

    def test_migrate_to_years_multiline_holdings(self):
        root = single("canada", 2024)
        (root / "holdings").mkdir()
        (root / "holdings" / "demo.toml").write_text("[meta]\n")
        p = root / "taxjson.toml"
        p.write_text(p.read_text().replace(
            '[accounts.margin]\ntype = "taxable"\n',
            '[accounts.margin]\ntype = "taxable"\nholdings = [\n'
            '  "inputs/margin/x.toml",\n  "holdings/demo.toml",\n]\n'))
        r = tjs("-C", str(root), "migrate", "--to-years")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        doc = tomllib.loads((root / "2024" / "taxjson.toml").read_text())
        self.assertEqual(doc["accounts"]["margin"]["holdings"],
                         ["../inputs/margin/x.toml", "holdings/demo.toml"])


class TestNewYearHeaders(unittest.TestCase):
    """#27: through the real new-year command."""

    def _new_year(self, header: str) -> str:
        top = multi("canada", years=(2024,))
        p = top / "2024" / "taxjson.toml"
        p.write_text(p.read_text().replace(
            "[accounts.margin]\n",
            "[estimate]\nother_income = 5\n\n"
            f"{header}\n").replace(
            'type = "taxable"\n',
            'type = "taxable"\nholdings = ["../2024/holdings/old.toml"]\n',
            1))
        r = tjs("-C", str(top), "new-year", "2025")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        return (top / "2025" / "taxjson.toml").read_text()

    def _check(self, text: str, name: str) -> None:
        doc = tomllib.loads(text)
        acct = doc["accounts"][name]
        self.assertEqual(acct["type"], "taxable")
        self.assertNotIn("holdings", acct)
        self.assertIn('# holdings = ["../2024/holdings/old.toml"]', text)
        self.assertNotIn("estimate", doc)

    def test_quoted_account_table(self):
        self._check(self._new_year('[accounts."margin-main"]'),
                    "margin-main")

    def test_hyphenated_account_table(self):
        self._check(self._new_year("[accounts.margin-main]"), "margin-main")

    def test_spaced_dotted_header(self):
        self._check(self._new_year("[ accounts . 'margin' ]  # main"),
                    "margin")


def _child(code: str) -> "subprocess.Popen":
    return subprocess.Popen([sys.executable, "-c", code],
                            stdout=subprocess.PIPE,
                            stderr=subprocess.DEVNULL)


class TestReadCapped(unittest.TestCase):
    """#29."""

    def test_child_closes_stdout_then_runs_on(self):
        from taxjson.lib.cra_slips import _read_capped
        p = _child("import os, time; os.close(1); time.sleep(30)")
        start = time.monotonic()
        out = _read_capped(p, 100, 0.5)
        self.assertLess(time.monotonic() - start, 10)
        self.assertEqual(out, (None, False))
        self.assertIsNotNone(p.returncode)          # killed and reaped
        self.assertTrue(p.stdout.closed)

    def test_blocked_reader(self):
        from taxjson.lib.cra_slips import _read_capped
        p = _child("import time; time.sleep(30)")
        start = time.monotonic()
        self.assertEqual(_read_capped(p, 100, 0.5), (None, False))
        self.assertLess(time.monotonic() - start, 10)
        self.assertIsNotNone(p.returncode)
        self.assertTrue(p.stdout.closed)

    def test_oversize_output(self):
        from taxjson.lib.cra_slips import _read_capped
        p = _child("import sys, time\nsys.stdout.write('x' * 300000)\n"
                   "sys.stdout.flush()\ntime.sleep(30)")
        start = time.monotonic()
        out, over = _read_capped(p, 1000, 20)
        self.assertTrue(over)
        self.assertEqual(len(out), 1000)
        self.assertLess(time.monotonic() - start, 10)
        self.assertIsNotNone(p.returncode)
        self.assertTrue(p.stdout.closed)

    def test_normal_exit(self):
        from taxjson.lib.cra_slips import _read_capped
        p = _child("print('slip')")
        self.assertEqual(_read_capped(p, 1000, 20), (b"slip\n", False))
        self.assertEqual(p.returncode, 0)
        self.assertTrue(p.stdout.closed)


if __name__ == "__main__":
    unittest.main()
