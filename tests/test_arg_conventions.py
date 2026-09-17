"""Argument-convention pins (AUDIT-2026-07-ui §1 axis A).

Covers the argparse conventions after the 2026-07 alignment and the
pre-1.0 compat purge:
- removed development-era spellings must FAIL, and live flags must
  work without deprecation noise;
- --taxable/--sheltered accept BOTH the historical `--taxable a b` shape and
  the repeatable `--taxable a --taxable b` shape (nargs='+' + extend);
- the PERIOD positional is optional everywhere: bare `taxjson events`
  defaults to the config tax year (like the -sum roll-ups), and a literal
  YYYY is a valid period token;
- --json on sum-gains / sum-income emits parseable JSON to stdout.
"""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def _run_mod(mod, *args, stdin_text=None):
    return subprocess.run(
        [sys.executable, "-m", f"taxjson.bin.{mod}", *args],
        cwd=REPO_ROOT, capture_output=True, text=True, input=stdin_text)


def _run_taxjson(root, *args):
    return subprocess.run(
        [sys.executable, "-m", "taxjson.bin.taxjson_run", "-C", str(root),
         *args],
        cwd=REPO_ROOT, capture_output=True, text=True)


def _tx(action, dt, symbol, qty, price, net, currency="CAD"):
    return dict(action=action, date=dt, time="09:30:00", symbol=symbol,
                quantity=qty, price=price, net_amount=net, currency=currency)


class TestRemovedAliasesStayRemoved(unittest.TestCase):
    """Development-era flag spellings were REMOVED pre-1.0 (no deployed
    users to break): the old names must now fail loudly instead of
    silently doing the polite thing."""

    def test_wash_radar_account_name_is_gone(self):
        r = _run_mod("taxjson_wash_radar", "--taxable", "/dev/null",
                     "--account-name", "margin")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("--account-name", r.stderr)   # named as unrecognized

    def test_fees_since_is_a_plain_documented_flag(self):
        # --since is the fees-sum PERIOD wrapper's cutoff channel — a
        # real flag now, with no deprecation note on use.
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "m_ib.json").write_text(json.dumps({
                "metadata": {"source_brokerage": "ib"},
                "transactions": [
                    {"id": "a", "action": "BUYSELL", "date": "2026-02-02",
                     "symbol": "A.US", "currency": "USD", "quantity": 10,
                     "commission": 7.0, "fee": 0.0, "net_amount": 100.0},
                    {"id": "b", "action": "BUYSELL", "date": "2025-02-02",
                     "symbol": "A.US", "currency": "USD", "quantity": 10,
                     "commission": 50.0, "fee": 0.0, "net_amount": 100.0},
                ]}))
            r = _run_mod("taxjson_fees", "--cache", tmp,
                         "--since", "2026-01-01")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("deprecated", r.stderr)
        self.assertIn("since 2026-01-01", r.stdout)   # filter applied
        self.assertNotIn("50.00", r.stdout)           # pre-cutoff dropped

    def test_fees_year_is_int_typed(self):
        r = _run_mod("taxjson_fees", "--cache", "/nonexistent",
                     "--year", "banana")
        self.assertEqual(r.returncode, 2)
        self.assertIn("invalid int value", r.stderr)


class TestExtendShapes(unittest.TestCase):
    """--taxable/--sheltered: `--taxable a b` == `--taxable a --taxable b`."""

    def _radar(self, *args):
        return _run_mod("taxjson_wash_radar", "--date", "2026-06-15", *args)

    def test_wash_radar_both_shapes_equal(self):
        with tempfile.TemporaryDirectory() as tmp:
            a = Path(tmp) / "a.json"
            b = Path(tmp) / "b.json"
            a.write_text(json.dumps({"transactions": [
                _tx("BUYSELL", "2026-01-05", "WSP.TO", 50, 100.0, 5000.0)]}))
            b.write_text(json.dumps({"transactions": [
                _tx("BUYSELL", "2026-02-05", "BNS.TO", 10, 60.0, 600.0)]}))
            legacy = self._radar("--taxable", str(a), str(b))
            repeat = self._radar("--taxable", str(a), "--taxable", str(b))
        self.assertEqual(legacy.returncode, repeat.returncode)
        self.assertEqual(legacy.stdout, repeat.stdout)
        self.assertTrue(legacy.stdout.strip())

    def test_safe_to_sell_and_lint_accept_repeated_flags(self):
        with tempfile.TemporaryDirectory() as tmp:
            a = Path(tmp) / "a.json"
            a.write_text(json.dumps({"transactions": [
                _tx("BUYSELL", "2026-01-05", "WSP.TO", 50, 100.0, 5000.0)]}))
            for mod in ("taxjson_safe_to_sell", "taxjson_lint_crosslistings"):
                one = _run_mod(mod, "--taxable", str(a))
                two = _run_mod(mod, "--taxable", str(a), "--taxable", str(a))
                self.assertEqual(one.returncode, 0, (mod, one.stderr))
                self.assertEqual(two.returncode, 0, (mod, two.stderr))

    def test_gains_sheltered_repeatable(self):
        with tempfile.TemporaryDirectory() as tmp:
            t = Path(tmp) / "t.json"
            s = Path(tmp) / "s.json"
            t.write_text(json.dumps({"transactions": [
                _tx("BUYSELL", "2026-01-05", "WSP.TO", 50, 100.0, 5000.0)]}))
            s.write_text(json.dumps({"transactions": []}))
            # Historical single-use shape and the new repeated shape both parse.
            one = _run_mod("taxjson_gains", "--country", "canada",
                           "--sheltered", str(s), str(t))
            two = _run_mod("taxjson_gains", "--country", "canada",
                           "--sheltered", str(s), "--sheltered", str(s),
                           str(t))
        self.assertEqual(one.returncode, 0, one.stderr)
        self.assertEqual(two.returncode, 0, two.stderr)
        self.assertEqual(one.stdout, two.stdout)  # empty sheltered book twice


class TestCountryDefaultNote(unittest.TestCase):
    def test_gains_notes_when_country_defaulted(self):
        with tempfile.TemporaryDirectory() as tmp:
            t = Path(tmp) / "t.json"
            t.write_text(json.dumps({"transactions": []}))
            defaulted = _run_mod("taxjson_gains", str(t))
            explicit = _run_mod("taxjson_gains", "--country", "canada", str(t))
        self.assertIn("taxjson-gains: note: --country not given; "
                      "assuming canada", defaulted.stderr)
        self.assertNotIn("assuming canada", explicit.stderr)
        self.assertEqual(defaulted.stdout, explicit.stdout)

    def test_carryover_notes_when_country_defaulted(self):
        with tempfile.TemporaryDirectory() as tmp:
            t = Path(tmp) / "t.json"
            t.write_text(json.dumps({"transactions": [
                _tx("BUYSELL", "2024-01-05", "WSP.TO", 50, 100.0, 5000.0)]}))
            r = _run_mod("taxjson_carryover", str(t))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("taxjson-carryover: note: --country not given; "
                      "assuming canada", r.stderr)


class TestOptionalPeriod(unittest.TestCase):
    """Bare `taxjson events` == `taxjson events <config-year>` ==
    `taxjson events tax_year` (period optional on the per-transaction views,
    same default flow as the -sum roll-ups; literal YYYY is a period token)."""

    def _project(self, tmp):
        root = Path(tmp)
        (root / "taxjson.toml").write_text(
            '[settings]\nyear = 2025\ncountry = "canada"\n'
            'base_currency = "CAD"\n')
        (root / "work").mkdir()
        (root / "work" / "margin_raw.json").write_text(json.dumps(
            {"transactions": [
                _tx("BUYSELL", "2025-03-01", "AAA.TO", 10, 5.0, 50.0),
                _tx("BUYSELL", "2024-03-01", "OLD.TO", 10, 5.0, 50.0),
            ]}))
        return root

    def test_bare_events_defaults_to_tax_year(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._project(tmp)
            bare = _run_taxjson(root, "events")
            year = _run_taxjson(root, "events", "2025")
            token = _run_taxjson(root, "events", "tax_year")
        self.assertEqual(bare.returncode, 0, bare.stderr)
        self.assertEqual(bare.stdout, year.stdout)
        self.assertEqual(bare.stdout, token.stdout)
        self.assertIn("AAA.TO", bare.stdout)
        self.assertNotIn("OLD.TO", bare.stdout)     # out-of-year row excluded

    def test_lone_account_positional_still_works(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._project(tmp)
            r = _run_taxjson(root, "events", "margin")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("AAA.TO", r.stdout)
        self.assertNotIn("OLD.TO", r.stdout)

    def test_year_token_on_gains_view_scope(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._project(tmp)
            r = _run_taxjson(root, "events", "2024")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("OLD.TO", r.stdout)
        self.assertNotIn("AAA.TO", r.stdout)

    def test_fees_sum_year_flag_was_removed(self):
        # The PERIOD positional is the only spelling; the development-
        # era `--year` alias was purged pre-1.0.
        with tempfile.TemporaryDirectory() as tmp:
            root = self._project(tmp)
            (root / "work" / "margin_ib.json").write_text(json.dumps({
                "metadata": {"source_brokerage": "ib"},
                "transactions": [
                    {"id": "a", "action": "BUYSELL", "date": "2024-02-02",
                     "symbol": "A.US", "currency": "USD", "quantity": 10,
                     "commission": 7.0, "fee": 0.0, "net_amount": 100.0}]}))
            flagged = _run_taxjson(root, "fees-sum", "--year", "2024")
            positional = _run_taxjson(root, "fees-sum", "2024")
        self.assertNotEqual(flagged.returncode, 0)
        self.assertIn("--year", flagged.stderr)
        self.assertEqual(positional.returncode, 0, positional.stderr)
        self.assertNotIn("note:", positional.stderr)

    def test_fees_sum_window_does_not_leak_since_note(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._project(tmp)
            (root / "work" / "margin_ib.json").write_text(json.dumps({
                "metadata": {"source_brokerage": "ib"},
                "transactions": [
                    {"id": "a", "action": "BUYSELL", "date": "2024-02-02",
                     "symbol": "A.US", "currency": "USD", "quantity": 10,
                     "commission": 7.0, "fee": 0.0, "net_amount": 100.0}]}))
            r = _run_taxjson(root, "fees-sum", "all")
            r30 = _run_taxjson(root, "fees-sum", "30d")
        self.assertEqual(r.returncode, 0, r.stderr)
        # The wrapper drives taxjson-fees --since internally for windows;
        # the deprecation note must not reach `taxjson fees-sum PERIOD` users.
        self.assertNotIn("--since", r30.stderr)


class TestSumJson(unittest.TestCase):
    def test_sum_gains_json_parseable(self):
        r = _run_mod("taxjson_sum_gains", "--json", stdin_text="{}")
        self.assertEqual(r.returncode, 0, r.stderr)
        doc = json.loads(r.stdout)
        self.assertIsInstance(doc, dict)

    def test_sum_income_json_parseable(self):
        r = _run_mod("taxjson_sum_income", "--json", stdin_text="{}")
        self.assertEqual(r.returncode, 0, r.stderr)
        doc = json.loads(r.stdout)
        self.assertIsInstance(doc, dict)

class TestSubcommandSynopsis(unittest.TestCase):
    """`taxjson <cmd> -h` must state what the command DOES, not just
    list options — argparse only prints `description` there, and
    add_parser doesn't inherit it from the listing's `help` text."""

    def _help(self, *cmd):
        import subprocess
        import sys
        from pathlib import Path
        r = subprocess.run(
            [sys.executable, "-m", "taxjson.bin.taxjson_run", *cmd,
             "-h"],
            cwd=Path(__file__).resolve().parent.parent,
            capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout

    def test_every_subcommand_has_a_description(self):
        # Structural check: every registered subcommand carries a
        # description after main()'s synopsis pass. Exercise via two
        # representative commands (full enumeration would spawn ~40
        # processes).
        out = self._help("run")
        self.assertIn("Run the full pipeline", out)
        out = self._help("fetch")
        self.assertIn("Download broker activity", out)

    def test_help_command_shows_it_too(self):
        import subprocess
        import sys
        from pathlib import Path
        r = subprocess.run(
            [sys.executable, "-m", "taxjson.bin.taxjson_run", "help",
             "watch"],
            cwd=Path(__file__).resolve().parent.parent,
            capture_output=True, text=True)
        self.assertIn("Report only what CHANGED", r.stdout)


class TestHelpWidth(unittest.TestCase):
    def test_help_capped_at_78_columns_on_wide_terminals(self):
        import os
        import subprocess
        import sys
        from pathlib import Path
        env = dict(os.environ, COLUMNS="220")
        for cmd in ([], ["run"], ["fetch"], ["watch"]):
            r = subprocess.run(
                [sys.executable, "-m", "taxjson.bin.taxjson_run",
                 *cmd, "-h"],
                cwd=Path(__file__).resolve().parent.parent,
                capture_output=True, text=True, env=env)
            self.assertEqual(r.returncode, 0, r.stderr)
            widest = max(len(ln) for ln in r.stdout.splitlines())
            self.assertLessEqual(
                widest, 79,
                f"{cmd or ['top-level']}: a {widest}-char help line — "
                f"the width cap (or the COMMAND metavar for the "
                f"choices blob) regressed")


if __name__ == "__main__":
    unittest.main()
