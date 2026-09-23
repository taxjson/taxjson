"""Tests for `taxjson init` — scaffolding a new, ready-to-populate project."""
import argparse
import io
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from taxjson.bin.taxjson_run import cmd_init, load_config

_CA_ACCOUNTS = ["crypto", "lira", "margin", "rrsp", "tfsa"]
_US_ACCOUNTS = ["401k", "crypto", "margin", "roth"]


def _init(path, force=False, country="canada", year=None):
    with redirect_stdout(io.StringIO()):           # mute the "Next:" banner
        cmd_init(argparse.Namespace(path=str(path), dir=".", force=force,
                                    country=country, year=year))


class TestInit(unittest.TestCase):
    def test_scaffolds_config_inputs_and_helpers(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "proj"
            _init(root)
            # Config + helper files.
            self.assertTrue((root / "taxjson.toml").exists())
            self.assertTrue((root / "ticker.map").exists())
            self.assertTrue((root / ".gitignore").exists())
            # An input folder per default account, each kept by a README.
            for acct in _CA_ACCOUNTS:
                self.assertTrue((root / "inputs" / acct / "README.txt").exists(),
                                f"missing README for {acct}")

    def test_generated_config_parses_with_expected_shape(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _init(root)
            cfg = load_config(root)
            self.assertIn("settings", cfg)
            for key in ("year", "country", "base_currency"):
                self.assertIn(key, cfg["settings"])
            self.assertEqual(sorted(cfg["accounts"]), _CA_ACCOUNTS)

    def test_canada_scaffold_values(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _init(root, country="canada")
            s = load_config(root)["settings"]
            self.assertEqual(s["country"], "canada")
            self.assertEqual(s["base_currency"], "CAD")
            self.assertEqual(s["tax_date"], "settle")
            self.assertEqual(s["source_currencies"], ["USD"])

    def test_usa_scaffold_values_and_accounts(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _init(root, country="usa")
            cfg = load_config(root)
            s = cfg["settings"]
            self.assertEqual(s["country"], "usa")
            self.assertEqual(s["base_currency"], "USD")
            self.assertEqual(s["tax_date"], "trade")
            self.assertEqual(s["source_currencies"], ["CAD"])
            self.assertEqual(sorted(cfg["accounts"]), _US_ACCOUNTS)
            # No Canadian account types in a US scaffold.
            self.assertNotIn("rrsp", cfg["accounts"])
            # 401k parses as a TOML bare key and is sheltered.
            self.assertEqual(cfg["accounts"]["401k"]["type"], "sheltered")
            self.assertEqual(cfg["accounts"]["crypto"]["type"], "taxable")
            for acct in _US_ACCOUNTS:
                self.assertTrue((root / "inputs" / acct / "README.txt").exists(),
                                f"missing README for {acct}")

    def test_year_defaults_to_current_and_overrides(self):
        from datetime import date
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _init(root)
            self.assertEqual(load_config(root)["settings"]["year"],
                             date.today().year)
            _init(root, force=True, year=2027)
            self.assertEqual(load_config(root)["settings"]["year"], 2027)

    def test_country_alias_normalized(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _init(root, country="us")
            self.assertEqual(load_config(root)["settings"]["country"], "usa")

    def test_unknown_country_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(SystemExit):
                _init(Path(tmp), country="germany")

    def test_ticker_map_stub_has_no_active_rules(self):
        # Every rule in the stub is commented, so the pipeline sees an
        # effectively empty map (no accidental renames on a fresh project).
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _init(root)
            active = [ln for ln in (root / "ticker.map").read_text().splitlines()
                      if ln.strip() and not ln.lstrip().startswith("#")]
            self.assertEqual(active, [])

    def test_refuses_to_overwrite_config_without_force(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _init(root)
            with self.assertRaises(SystemExit):
                _init(root)                      # taxjson.toml already exists

    def test_force_retemplates_config_but_preserves_user_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _init(root)
            # User edits the config and populates the ticker map.
            (root / "taxjson.toml").write_text("# my edits\n")
            (root / "ticker.map").write_text("GLOBAL FOO.US BAR.US\n")
            _init(root, force=True)
            # Config re-templated (user edit gone), ticker.map untouched.
            self.assertIn("[settings]", (root / "taxjson.toml").read_text())
            self.assertEqual((root / "ticker.map").read_text(),
                             "GLOBAL FOO.US BAR.US\n")




class TestScaffoldCoversCurrentFeatures(unittest.TestCase):
    """The scaffold drifted behind a week of new config keys — a fresh
    project never mentioned province (which `estimate` requires),
    fetch, fx-cash or instalments."""

    def _toml(self, country):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "proj"
            _init(root, country=country)
            return (root / "taxjson.toml").read_text()

    def test_canada_scaffold_mentions_the_current_features(self):
        t = self._toml("canada")
        for key in ("province", "fx_cash_gains",
                    "brokerage", "query_id", "[instalments]",
                    "prior_year_net_tax", "holdings",
                    "option_premium_timing", "option_grant_timing_since",
                    "option_buyback_loss_superficial"):
            self.assertIn(key, t, key)

    def test_us_scaffold_omits_the_canadian_option_switches(self):
        # ITA s.49(1) timing is Canada-only; the US engine always nets
        # at close, so the US scaffold must not advertise the switches.
        t = self._toml("usa")
        self.assertNotIn("option_premium_timing", t)
        self.assertIn("holdings", t)

    def test_scaffold_keys_are_column_aligned(self):
        # The point of the layout: two projects' files diff only where
        # their values differ, so every [settings] comment starts in
        # the same column.
        t = self._toml("canada")
        block = t.split("[settings]")[1].split("\n\n")[0]
        cols = {line.index("#") for line in block.splitlines()
                if "#" in line and not line.startswith("#")}
        self.assertEqual(len(cols), 1, block)

    def test_scaffold_activates_nothing_new(self):
        # Every addition is a COMMENT: the parsed config is unchanged,
        # so a fresh project behaves exactly as before.
        from taxjson.lib.tomlcompat import tomllib
        for country in ("canada", "usa"):
            doc = tomllib.loads(self._toml(country))
            self.assertEqual(sorted(doc), ["accounts", "settings"])
            for key in ("province", "fx_cash_gains"):
                self.assertNotIn(key, doc["settings"], key)
            for acct in doc["accounts"].values():
                self.assertNotIn("brokerage", acct)

    def test_us_scaffold_omits_the_canadian_instalment_block(self):
        t = self._toml("usa")
        self.assertNotIn("instalments", t)
        self.assertIn("§988", t)      # the US fx-cash rule instead


class TestScaffoldCommentsAreValidToml(unittest.TestCase):
    """The scaffold shipped a multi-line INLINE TABLE — legal-looking
    but invalid TOML — so uncommenting the instalments block exactly
    as its own comments instruct produced a TOMLDecodeError. The
    headline feature was unreachable from the tool's own scaffold."""

    def test_uncommenting_the_instalments_block_parses(self):
        from taxjson.lib.tomlcompat import tomllib
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "proj"
            _init(root, country="canada")
            text = (root / "taxjson.toml").read_text()
        lines = text.splitlines()
        start = next(i for i, ln in enumerate(lines)
                     if ln.strip().startswith("# [instalments]"))
        out = []
        for ln in lines[start:]:
            st = ln.strip()
            if not st:
                continue
            if not st.startswith("#"):
                break
            body = st[1:]
            body = body[1:] if body.startswith(" ") else body
            if body.lstrip().startswith("#"):    # prose / alternative
                continue
            out.append(body)
        block = "\n".join(out)
        doc = tomllib.loads(block)               # must not raise
        self.assertEqual(len(doc["instalments"]["paid"]), 2)
        self.assertEqual(doc["instalments"]["basis"], "current_year")
        self.assertIn("note", doc["instalments"]["paid"][1])


if __name__ == "__main__":
    unittest.main()
