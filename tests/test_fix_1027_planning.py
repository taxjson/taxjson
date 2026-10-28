"""The tjs command audit of 2026-10-27, planning commands: wash-radar,
watch, rebuy, buy-check and sell-check (backlog TJ-0303, TJ-0304,
TJ-0330). Synthetic data only: made-up tickers (SYN.TO, SYNOLD.US,
ZZCOIN) in throwaway projects."""
import json
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

from _qa_project import tj

_PLANNING = ("wash-radar", "watch", "rebuy", "buy-check", "sell-check")


def _toml(country, *, crypto=False, sheltered=False):
    zone = "America/New_York" if country == "usa" else "America/Toronto"
    cur = "USD" if country == "usa" else "CAD"
    text = (f'[settings]\nlocal_timezone = "{zone}"\nyear = '
            f'{date.today().year}\ncountry = "{country}"\n'
            f'base_currency = "{cur}"\nsource_currencies = []\n'
            '[accounts.margin]\ntype = "taxable"\n')
    if crypto:
        text += "crypto = true\n"
    if sheltered:
        text += '[accounts.rrsp]\ntype = "sheltered"\n'
    return text


def _ago(days):
    return (date.today() - timedelta(days=days)).isoformat()


class TestUsCryptoOnlyJson(unittest.TestCase):
    """TJ-0330: a US project whose only taxable account is crypto has
    nothing the wash-sale rule reaches (US-WASH-13). The planning
    commands said so in prose on stdout under --json too."""

    @classmethod
    def setUpClass(cls):
        cls._td = tempfile.TemporaryDirectory()
        cls.root = Path(cls._td.name)
        (cls.root / "taxjson.toml").write_text(_toml("usa", crypto=True))
        (cls.root / "inputs" / "margin").mkdir(parents=True)
        (cls.root / "inputs" / "margin" / "m.tt").write_text(
            f"BUYSELL {_ago(60)} 10:00:00 ZZCOIN 10 USD 10 100 0\n")
        tj(cls.root, "run", "--no-input")

    @classmethod
    def tearDownClass(cls):
        cls._td.cleanup()

    def _json(self, *args):
        r = tj(self.root, *args, "--json", check=False)
        self.assertEqual(r.returncode, 0, r.stderr)
        try:
            return json.loads(r.stdout)
        except ValueError:
            self.fail(f"{args}: not JSON: {r.stdout!r}")

    def test_tj0330_each_command_prints_its_document(self):
        for cmd in _PLANNING:
            args = (cmd, "ZZCOIN") if cmd in ("buy-check",
                                              "sell-check") else (cmd,)
            with self.subTest(cmd=cmd):
                doc = self._json(*args)
                self.assertIn("no wash-checkable taxable account",
                              doc["not_applicable"])
                self.assertIn("scope_note", doc)
        self.assertEqual(self._json("wash-radar")["sections"], [])
        self.assertEqual(self._json("buy-check", "ZZCOIN")["results"], [])
        self.assertEqual(self._json("sell-check", "ZZCOIN")["results"], [])
        rebuy = self._json("rebuy")
        self.assertEqual((rebuy["rows"], rebuy["cleared"]), ([], []))
        watch = self._json("watch")
        self.assertEqual((watch["changes"], watch["tracked"]), ([], 0))
        # Nothing was observed: no baseline is recorded.
        self.assertFalse((self.root / "work" / ".watch_state.json").exists())

    def test_tj0330_a_us_crypto_account_named(self):
        doc = self._json("wash-radar", "margin")
        self.assertIn("margin is a crypto account of a US project",
                      doc["not_applicable"])
        self.assertEqual(doc["account"], "margin")

    def test_tj0330_text_is_one_line_as_before(self):
        for cmd in ("wash-radar", "watch"):
            with self.subTest(cmd=cmd):
                r = tj(self.root, cmd, check=False)
                self.assertEqual(r.returncode, 0, r.stderr)
                self.assertIn(f"taxjson {cmd}: no wash-checkable taxable "
                              "account", r.stdout)


class TestWatchIncompleteContext(unittest.TestCase):
    """TJ-0303: a LOCKED baseline (an RRSP bought SYN.TO 7 days ago),
    then work/sheltered_base.json goes missing. `tjs watch` reported
    LOCKED -> CLEAR, "safe to sell at a loss", and replaced the
    baseline with that state."""

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.root = root = Path(self._td.name)
        (root / "taxjson.toml").write_text(_toml("canada", sheltered=True))
        for acct, days in (("margin", 60), ("rrsp", 7)):
            (root / "inputs" / acct).mkdir(parents=True)
            (root / "inputs" / acct / "m.tt").write_text(
                f"BUYSELL {_ago(days)} 10:00:00 SYN.TO 10 CAD 10 100 0\n")
        tj(root, "run", "--no-input")
        self.state = root / "work" / ".watch_state.json"
        self.shelter = root / "work" / "sheltered_base.json"

    def test_tj0303_nothing_compared_and_the_baseline_kept(self):
        first = json.loads(tj(self.root, "watch", "--json").stdout)
        self.assertTrue(first["baseline"])
        self.assertEqual((first["actionable"], first["incomplete"]), (1, []))
        saved = self.state.read_text()
        self.shelter.rename(self.shelter.with_suffix(".saved"))
        r = tj(self.root, "watch", "--json", check=False)
        self.assertEqual(r.returncode, 0, r.stderr)
        doc = json.loads(r.stdout)
        self.assertEqual(doc["changes"], [])
        self.assertEqual(doc["incomplete"], ["work/sheltered_base.json"])
        self.assertIsNone(doc["tracked"])
        self.assertEqual(doc["since"], first["as_of"])
        self.assertIn("Warning: nothing compared: work/sheltered_base.json "
                      "is missing", r.stderr)
        self.assertEqual(self.state.read_text(), saved)
        # Text: the warning only, no CLEAR line, and --exit-code finds
        # no change to report.
        r = tj(self.root, "watch", "--exit-code", check=False)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout, "")
        self.assertIn(f"the baseline of {first['as_of']} is kept", r.stderr)
        self.assertEqual(self.state.read_text(), saved)
        # The books back: compared against the kept baseline, quiet.
        self.shelter.with_suffix(".saved").rename(self.shelter)
        doc = json.loads(tj(self.root, "watch", "--json").stdout)
        self.assertEqual((doc["changes"], doc["incomplete"]), ([], []))
        self.assertEqual(doc["actionable"], 1)

    def test_tj0303_no_baseline_recorded_from_incomplete_books(self):
        self.shelter.unlink()
        r = tj(self.root, "watch", check=False)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("no baseline is recorded", r.stderr)
        self.assertFalse(self.state.exists())

    def test_tj0303_a_taxable_accounts_missing_books(self):
        # A second taxable account whose books are gone hides its buys
        # the same way: named, nothing compared.
        cfg = self.root / "taxjson.toml"
        cfg.write_text(cfg.read_text()
                       + '[accounts.margin2]\ntype = "taxable"\n')
        (self.root / "inputs" / "margin2").mkdir()
        (self.root / "inputs" / "margin2" / "m.tt").write_text(
            f"BUYSELL {_ago(5)} 10:00:00 SYN.TO 5 CAD 10 50 0\n")
        tj(self.root, "run", "--no-input")
        (self.root / "work" / "margin2_base.json").unlink()
        r = tj(self.root, "watch", "--json", check=False)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(json.loads(r.stdout)["incomplete"],
                         ["work/margin2_base.json"])
        self.assertFalse(self.state.exists())
        # One warning, not the radar's "no books for taxable account(s)"
        # as well.
        self.assertNotIn("no books for taxable account", r.stderr)


class TestMalformedMapRefused(unittest.TestCase):
    """TJ-0304: SYN.TO sold at a loss 7 days ago; `GLOBAL SYNOLD.US
    SYN.TO` makes SYNOLD.US the same security, so buying it is UNSAFE.
    With the line broken (`GLOBAL SYNOLD.US`, no target) the loader
    dropped it with a warning and buy-check answered SAFE, exit 0."""

    @classmethod
    def setUpClass(cls):
        cls._td = tempfile.TemporaryDirectory()
        cls.root = root = Path(cls._td.name)
        (root / "taxjson.toml").write_text(_toml("canada"))
        (root / "inputs" / "margin").mkdir(parents=True)
        (root / "inputs" / "margin" / "m.tt").write_text(
            f"BUYSELL {_ago(60)} 10:00:00 SYN.TO 10 CAD 10 100 0\n"
            f"BUYSELL {_ago(7)} 10:00:00 SYN.TO -10 CAD 5 50 0\n")
        (root / "ticker.map").write_text("GLOBAL SYNOLD.US SYN.TO\n")
        tj(root, "run", "--no-input")

    @classmethod
    def tearDownClass(cls):
        cls._td.cleanup()

    def setUp(self):
        self.map = self.root / "ticker.map"
        self.addCleanup(self.map.write_text, "GLOBAL SYNOLD.US SYN.TO\n")

    def test_tj0304_the_valid_map_gives_unsafe(self):
        r = tj(self.root, "buy-check", "SYNOLD.US", "--json", check=False)
        self.assertEqual(r.returncode, 1, r.stderr)
        self.assertEqual(json.loads(r.stdout)["results"][0]["verdict"],
                         "UNSAFE")

    def test_tj0304_a_malformed_line_refuses_every_check(self):
        self.map.write_text("GLOBAL SYNOLD.US\n")
        for args in (("buy-check", "SYNOLD.US", "--json"),
                     ("buy-check", "SYNOLD.US"),
                     ("sell-check", "SYN.TO", "--json"),
                     ("rebuy", "SYNOLD.US", "--json")):
            with self.subTest(args=args):
                r = tj(self.root, *args, check=False)
                self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
                self.assertEqual(r.stdout, "")
                self.assertIn("Error: 1 ticker.map problem(s)", r.stderr)
                self.assertIn("ticker.map:1: GLOBAL line needs `from to`",
                              r.stderr)
                self.assertNotIn("SAFE", r.stdout)

    def test_tj0304_a_tobase_map_problem_refuses_too(self):
        # The effective map is ticker.map with tobase.map's pairs.
        (self.root / "tobase.map").write_text("TOBASE SYNOLD.US\n")
        self.addCleanup((self.root / "tobase.map").unlink)
        r = tj(self.root, "buy-check", "SYNOLD.US", check=False)
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("tobase.map problem(s)", r.stderr)


if __name__ == "__main__":
    unittest.main()
