"""`tjs audit --account X` on a crypto account (issue #79).

Two taxable crypto accounts in a Canadian project: a buys 1 SYN at
10,000, b buys 1 SYN at 20,000 (pooled ACB 15,000 across exchanges,
CA-SL-13), a sells 1 at 30,000 — the run's gain is 15,000. The
--account filter used to drop b from the recomputation, so audit
re-ran a's own cost (gain 20,000), reported a pipeline mismatch and
exited 1 on fresh books. The filter is a display filter: the
computation is the one the run made.

The US keeps basis per account and never blends crypto books
(US-BASIS-01): there the filtered audit is a's own books and ties too.
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tax_rules import rule

REPO_ROOT = Path(__file__).resolve().parent.parent


def _tx(d, q, n, sym="SYN"):
    return f"BUYSELL {d} 10:00:00 {sym} {q} CAD {n / abs(q)} {n} 0\n"


BOOKS = {
    "a": _tx("2025-01-06", 1, 10000) + _tx("2025-06-02", -1, 30000),
    "b": _tx("2025-02-03", 1, 20000),
}


def _project(td, country="canada"):
    root = Path(td)
    cur = "USD" if country == "usa" else "CAD"
    cfg = (f'[settings]\nyear = 2025\ncountry = "{country}"\n'
           f'base_currency = "{cur}"\nsource_currencies = []\n'
           f'local_timezone = "America/Toronto"\n')
    for name, body in BOOKS.items():
        cfg += f'[accounts.{name}]\ntype = "taxable"\ncrypto = true\n'
        d = root / "inputs" / name
        d.mkdir(parents=True)
        (d / "trades.tt").write_text(
            body.replace(" CAD ", f" {cur} "))
    (root / "taxjson.toml").write_text(cfg)
    return root


def _cli(root, *a):
    env = {**os.environ, "TAXJSON_OFFLINE": "1", "TAXJSON_WIDTH": "0",
           "PYTHONDONTWRITEBYTECODE": "1"}
    return subprocess.run([sys.executable, "-m", "taxjson.bin.taxjson_run",
                           "-C", str(root), *a], cwd=REPO_ROOT, env=env,
                          capture_output=True, text=True,
                          stdin=subprocess.DEVNULL, timeout=300)


def _audit_gain(root, *extra):
    r = _cli(root, "audit", "--json", *extra)
    doc = json.loads(r.stdout) if r.stdout.strip() else {}
    gains = [e.get("gain") for e in doc.get("events") or []]
    return r, gains


class TestAuditAccountCrypto(unittest.TestCase):
    @rule("CA-SL-13")
    def test_canada_account_filter_keeps_the_crypto_blend(self):
        with tempfile.TemporaryDirectory() as td:
            root = _project(td)
            r = _cli(root, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stdout[-2000:] + r.stderr)
            full = _cli(root, "audit", "--summary")
            self.assertEqual(full.returncode, 0,
                             full.stdout[-2000:] + full.stderr)
            one = _cli(root, "audit", "--account", "a", "--summary")
            self.assertEqual(one.returncode, 0,
                             one.stdout[-2000:] + one.stderr)
            self.assertIn("+15,000.00", one.stdout)
            self.assertNotIn("MISMATCHED, 0 not found  ✗", one.stdout)
            self.assertNotIn("FAILED", one.stdout)
            r, gains = _audit_gain(root, "--account", "a")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(len(gains), 1)
            self.assertAlmostEqual(float(gains[0]), 15000.0, places=2)
            # b sold nothing: the filter still narrows what is shown.
            b = _cli(root, "audit", "--account", "b", "--summary")
            self.assertNotEqual(b.returncode, 0)
            self.assertIn("no disposition matches --account b", b.stderr)

    @rule("US-BASIS-01")
    def test_usa_account_filter_is_the_accounts_own_books(self):
        with tempfile.TemporaryDirectory() as td:
            root = _project(td, country="usa")
            r = _cli(root, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stdout[-2000:] + r.stderr)
            one = _cli(root, "audit", "--account", "a", "--summary")
            self.assertEqual(one.returncode, 0,
                             one.stdout[-2000:] + one.stderr)
            r, gains = _audit_gain(root, "--account", "a")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(len(gains), 1)
            self.assertAlmostEqual(float(gains[0]), 20000.0, places=2)


if __name__ == "__main__":
    unittest.main()
