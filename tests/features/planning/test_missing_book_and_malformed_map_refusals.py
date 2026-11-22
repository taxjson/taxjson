"""The 2026-10-29 fix round, planning commands: buy-check, sell-check,
rebuy, wash-radar, harvest and wash-sales (backlog TJ-0350, TJ-0351,
TJ-0352, TJ-0243, TJ-0245, TJ-0247, TJ-0244, TJ-0246, TJ-0258,
TJ-0259, TJ-0413, TJ-0157). Synthetic data only: made-up tickers
(SYN.TO, ONE.TO, TWO.TO, ZZU.US) in throwaway projects.

Formerly: test_fix_1029_planning"""
import json
import tempfile
import unittest
from pathlib import Path

from _qa_project import tj
from _support.planning_toml import _toml, _ago


class TestMissingShelteredBookRefused(unittest.TestCase):
    """TJ-0350 (the audit's case TJ-0394): an RRSP bought SYN.TO 7 days
    ago, so a loss sale of the taxable SYN.TO is UNSAFE. With
    work/sheltered_base.json gone, sell-check said SAFE, exit 0, after
    an Info line. Decided 2026-10-29: sell-check, buy-check and rebuy
    refuse, exit 2, run `tjs run` first.

    Formerly: test_fix_1029_planning.TestMissingShelteredBookRefused"""

    @classmethod
    def setUpClass(cls):
        cls._td = tempfile.TemporaryDirectory()
        cls.root = root = Path(cls._td.name)
        (root / "taxjson.toml").write_text(_toml(sheltered=True))
        for acct, days in (("margin", 60), ("rrsp", 7)):
            (root / "inputs" / acct).mkdir(parents=True)
            (root / "inputs" / acct / "m.tt").write_text(
                f"BUYSELL {_ago(days)} 10:00:00 SYN.TO 10 CAD 10 100 0\n")
        tj(root, "run", "--no-input")
        cls.shelter = root / "work" / "sheltered_base.json"

    @classmethod
    def tearDownClass(cls):
        cls._td.cleanup()

    def test_tj0350_with_the_book_sell_check_is_unsafe(self):
        r = tj(self.root, "sell-check", "SYN.TO", "--json", check=False)
        self.assertEqual(r.returncode, 1, r.stderr)
        self.assertEqual(json.loads(r.stdout)["results"][0]["verdict"],
                         "UNSAFE")

    def test_tj0350_without_it_every_check_refuses(self):
        saved = self.shelter.with_suffix(".saved")
        self.shelter.rename(saved)
        self.addCleanup(saved.rename, self.shelter)
        for args in (("sell-check", "SYN.TO", "--json"),
                     ("sell-check", "SYN.TO"),
                     ("buy-check", "SYN.TO", "--json"),
                     ("rebuy", "--json"),
                     ("rebuy", "SYN.TO")):
            with self.subTest(args=args):
                r = tj(self.root, *args, check=False)
                self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
                self.assertEqual(r.stdout, "")
                err = " ".join(r.stderr.split())
                self.assertIn("Error: no sheltered_base.json in work/", err)
                self.assertIn("Run `tjs run` first", err)

    def test_tj0394_wash_sales_explain_refuses_too(self):
        # The explain replay lost the registered account's denial the
        # saved table still holds.
        saved = self.shelter.with_suffix(".saved")
        self.shelter.rename(saved)
        self.addCleanup(saved.rename, self.shelter)
        r = tj(self.root, "wash-sales", "--explain", check=False)
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("no sheltered_base.json in work/", r.stderr)


class TestUnreadableMapExit2(unittest.TestCase):
    """TJ-0351 (the audit's case TJ-0405): a ticker.map that is not
    UTF-8 exited 1, the UNSAFE verdict's status; a problem line exits
    2. Both are an input to fix: exit 2.

    Formerly: test_fix_1029_planning.TestUnreadableMapExit2"""

    @classmethod
    def setUpClass(cls):
        cls._td = tempfile.TemporaryDirectory()
        cls.root = root = Path(cls._td.name)
        (root / "taxjson.toml").write_text(_toml())
        (root / "inputs" / "margin").mkdir(parents=True)
        (root / "inputs" / "margin" / "m.tt").write_text(
            f"BUYSELL {_ago(60)} 10:00:00 SYN.TO 10 CAD 10 100 0\n")
        tj(root, "run", "--no-input")
        (root / "ticker.map").write_bytes(b"GLOBAL SYNOLD.US SYN.TO \xff\xfe\n")

    @classmethod
    def tearDownClass(cls):
        cls._td.cleanup()

    def test_tj0351_unreadable_map_exits_2(self):
        for args in (("buy-check", "SYN.TO"), ("sell-check", "SYN.TO"),
                     ("rebuy", "--json")):
            with self.subTest(args=args):
                r = tj(self.root, *args, check=False)
                self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
                self.assertIn("ticker.map cannot be read", r.stderr)


class TestHarvestStrictMap(unittest.TestCase):
    """TJ-0352 (the audit's case TJ-0399): the books hold
    NEW270115C00010000.TO, built from the OLD contract held through
    `GLOBAL OLD.TO NEW.TO`. With the line broken (`GLOBAL OLD.TO`) the
    lenient loader dropped it, harvest quoted the NEW contract (5.00)
    instead of the OLD one held (0.50): a loss of 50 read as a gain of
    400, exit 0.

    Formerly: test_fix_1029_planning.TestHarvestStrictMap"""

    OLD, NEW = "OLD270115C00010000.TO", "NEW270115C00010000.TO"

    def _harvest(self, mapping):
        import contextlib
        import io
        import os
        from unittest import mock
        from taxjson.bin.taxjson_harvest import main as harvest
        with tempfile.TemporaryDirectory() as td:
            p = Path(td)

            def book(sym):
                return {"summary": {"year": 2026}, "transactions": [],
                        "inventory": [{"symbol": sym, "qty": 1,
                                       "total_cost": 100,
                                       "currency": "CAD",
                                       "position_start_date":
                                           "2025-01-02"}]}
            (p / "margin_gains.json").write_text(json.dumps(book(self.NEW)))
            (p / "margin_raw_gains.json").write_text(
                json.dumps(book(self.OLD)))
            (p / "ticker.map").write_text(mapping)
            asked = []

            def fetch(rem):
                asked.extend(rem)
                return {s: (0.5 if s.startswith("OLD") else 5.0,
                            "synthetic", "CAD") for s in rem}
            out, err = io.StringIO(), io.StringIO()
            env = {k: v for k, v in os.environ.items()
                   if k != "TAXJSON_OFFLINE"}
            with mock.patch.dict(os.environ, env, clear=True), \
                    contextlib.redirect_stdout(out), \
                    contextlib.redirect_stderr(err):
                rc = harvest([str(p / "margin_gains.json"), "--ticker-map",
                              str(p / "ticker.map"), "--options",
                              "--country", "canada", "--json", "--no-ibkr",
                              "--price-cache", str(p / "prices.json")],
                             fetchers=[lambda rem: {}],
                             option_fetchers=[fetch])
        return rc, asked, out.getvalue(), err.getvalue()

    def test_tj0352_valid_map_quotes_the_contract_held(self):
        rc, asked, out, _err = self._harvest("GLOBAL OLD.TO NEW.TO\n")
        self.assertEqual(rc, 0)
        self.assertEqual(asked, [self.OLD])

    def test_tj0352_malformed_map_quotes_no_other_contract(self):
        rc, asked, out, err = self._harvest("GLOBAL OLD.TO\n")
        self.assertEqual(rc, 0, err)
        self.assertEqual(asked, [])
        self.assertNotIn("400", out)
        self.assertIn("option position(s) omitted", " ".join(err.split()))


class TestCommandsRefuseMalformedMap(unittest.TestCase):
    """TJ-0352: `tjs harvest` and `tjs wash-radar` (whose SIMILAR groups
    lib/similar read past a dropped line) refuse a map with a problem
    line, exit 2, as `tjs run` and buy-check do.

    Formerly: test_fix_1029_planning.TestCommandsRefuseMalformedMap"""

    @classmethod
    def setUpClass(cls):
        cls._td = tempfile.TemporaryDirectory()
        cls.root = root = Path(cls._td.name)
        (root / "taxjson.toml").write_text(_toml())
        (root / "inputs" / "margin").mkdir(parents=True)
        (root / "inputs" / "margin" / "m.tt").write_text(
            f"BUYSELL {_ago(60)} 10:00:00 SYN.TO 10 CAD 10 100 0\n"
            f"BUYSELL {_ago(7)} 10:00:00 SYN.TO -5 CAD 5 25 0\n")
        (root / "ticker.map").write_text("SIMILAR SYN.TO SYNB.TO\n")
        tj(root, "run", "--no-input")
        (root / "ticker.map").write_text("SIMILAR SYN.TO\n")

    @classmethod
    def tearDownClass(cls):
        cls._td.cleanup()

    def test_tj0352_each_command_refuses(self):
        for args in (("harvest", "--no-ibkr"), ("wash-radar",),
                     ("wash-radar", "--json"), ("wash-radar", "margin")):
            with self.subTest(args=args):
                r = tj(self.root, *args, check=False)
                self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
                self.assertIn("Error: 1 ticker.map problem(s)", r.stderr)
                self.assertIn("ticker.map:1:", r.stderr)

    def test_tj0352_open_findings_kept_while_the_map_is_broken(self):
        from taxjson.lib import similar as SIM
        state = {"format": SIM.FORMAT, "country": "canada",
                 "findings": [{"loss_symbol": "SYN.TO",
                               "other_symbol": "SYNB.TO"}]}
        work = self.root / "work"
        (work / SIM.STATE).write_text(json.dumps(state))
        self.addCleanup((work / SIM.STATE).unlink)
        self.assertEqual(len(SIM.open_findings(self.root)), 1)
        self.assertTrue(SIM.map_problems(self.root))


if __name__ == "__main__":
    unittest.main()
