"""The 2026-10-29 fix round 6, frozen group: the review of a filed
year's shared inputs (check-filed, the run's drift check, `years`).

- TJ-0707 a filed year's shared exports are read as the parsers read
  them (brokerages/base.decode_broker_text): a corrected sale in a
  UTF-16 export is named; a file that cannot be read is named as not
  compared, never passed as unchanged;
- TJ-0708 a generic mapping (<csv>.toml, generic.toml) corrected in
  the shared folder after the close is named: the CSV's bytes are the
  same, its figures are not.

Synthetic data only (made-up tickers and accounts, amounts under a
thousand).
"""
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from taxjson.bin import taxjson_run as run
from taxjson.lib import project_layout as PL
from taxjson.lib.dispatch import python_module_argv

from test_fix_a2_ib_dates import FII_H, _stock_fii
from test_fix_ibparse import HEAD, TRADES_H, _trade

REPO_ROOT = Path(__file__).resolve().parent.parent
_PYPATH = os.pathsep.join(str(REPO_ROOT / p) for p in (
    "src", "packages/taxjson-fetch/src"))


def _env():
    env = dict(os.environ, TAXJSON_WIDTH="0", NO_COLOR="1",
               TAXJSON_OFFLINE="1")
    env["PYTHONPATH"] = _PYPATH + os.pathsep + env.get("PYTHONPATH", "")
    return env


def _cli(root, *args, cwd=None):
    """`taxjson -C ROOT ARGS` as a child of THIS tree's code (`root`
    None: no -C, run in `cwd`)."""
    pre = [] if root is None else ["-C", str(root)]
    cmd = python_module_argv("taxjson.bin.taxjson_run",
                             [*pre, *map(str, args)])
    return subprocess.run(cmd, cwd=cwd or REPO_ROOT, capture_output=True,
                          text=True, stdin=subprocess.DEVNULL,
                          env=_env(), timeout=900)


def _tmp(test):
    td = tempfile.TemporaryDirectory()
    test.addCleanup(td.cleanup)
    return Path(td.name)


def _said(r):
    return " ".join((r.stdout + r.stderr).split())


# An IB activity statement (made-up account and ticker): a buy and a
# sale of 2024.
_IB = (HEAD + FII_H + _stock_fii("ZZS", "TSE") + TRADES_H
       + _trade("ZZS", "2024-01-03, 10:00:00", 10, 10, -100, cur="CAD",
                acct="U1234567")
       + _trade("ZZS", "2024-02-05, 10:00:00", -10, 20, 200, cur="CAD",
                code="C", acct="U1234567"))
_IB_SALE, _IB_SALE_FIXED = "-10,20,0,200", "-10,25,0,250"
# A generic CSV with two amount columns, and its mapping.
_GEN = ("Date,Action,Symbol,Quantity,Amount,CorrectedAmount,Currency\n"
        "2024-03-04,BUY,ZZG.TO,10,-100,-100,CAD\n"
        "2024-06-05,SELL,ZZG.TO,10,200,250,CAD\n")
_MAP = ('[columns]\ndate = "Date"\naction = "Action"\nsymbol = "Symbol"\n'
        'quantity = "Quantity"\namount = "Amount"\n'
        'currency = "Currency"\n\n'
        '[actions]\nBUY = "buy"\nSELL = "sell"\n')
_MAP_FIXED = _MAP.replace('amount = "Amount"', 'amount = "CorrectedAmount"')
_ROW_25 = "2025-02-03,SELL,ZZH.TO,5,105,105,CAD\n"


# --------------------------------------------- the comparison by itself
class TestSharedInputsMoved(unittest.TestCase):
    """_shared_inputs_moved on a hand-made frozen 2024 year: its copy
    in 2024/inputs/<account>/, the shared folder ../inputs/."""

    def setUp(self):
        top = _tmp(self)
        self.root = top / "2024"
        self.shared = top / "inputs"
        (self.root / "inputs" / "margin").mkdir(parents=True)
        (self.shared / "margin").mkdir(parents=True)
        (self.root / "taxjson.toml").write_text(
            '[settings]\nyear = 2024\ncountry = "canada"\n\n'
            '[accounts.margin]\ntype = "taxable"\n')
        self.fz = {"inputs_dir": "../inputs", "inputs": {}}

    def frozen(self, name, data):
        raw = data if isinstance(data, bytes) else data.encode()
        (self.root / "inputs" / "margin" / name).write_bytes(raw)
        (self.shared / "margin" / name).write_bytes(raw)
        self.fz["inputs"][f"inputs/margin/{name}"] = "made-up-hash"

    def shared_file(self, name, data):
        raw = data if isinstance(data, bytes) else data.encode()
        (self.shared / "margin" / name).write_bytes(raw)

    def moved(self):
        return run._shared_inputs_moved(self.root, self.fz, 2024)

    # TJ-0707 ------------------------------------------------------
    def test_a_utf16_export_corrected_is_named(self):
        """The audit's case: rows of a UTF-16 export read as UTF-8 had
        no date, so both sides were empty and the correction no
        change."""
        self.frozen("activity.csv", _IB.encode("utf-16"))
        self.shared_file("activity.csv", _IB.replace(
            _IB_SALE, _IB_SALE_FIXED).encode("utf-16"))
        got = self.moved()
        self.assertEqual(got["exports"], ["../inputs/margin/activity.csv"])
        self.assertEqual(got["unread"], [])
        self.assertEqual(run._shared_exports_moved(self.root, self.fz, 2024),
                         ["../inputs/margin/activity.csv"])

    def test_a_utf16_export_new_since_the_close_is_named(self):
        self.frozen("trades.csv", _GEN)
        self.shared_file("late.csv", _IB.encode("utf-16"))
        self.assertEqual(self.moved()["exports"],
                         ["../inputs/margin/late.csv"])

    def test_a_utf8_resave_of_the_same_rows_is_no_change(self):
        self.frozen("activity.csv", _IB.encode("utf-16"))
        self.shared_file("activity.csv", "﻿" + _IB)
        self.assertEqual(self.moved(),
                         {"exports": [], "mappings": [], "unread": []})

    def test_an_export_that_cannot_be_read_is_named(self):
        """A re-save in a legacy encoding (cp1252): not judged, so
        named — never an OK that compared nothing."""
        self.frozen("activity.csv", _IB)
        self.shared_file("activity.csv", (_IB + "Notes,Data,Activité\n")
                         .encode("cp1252"))
        got = self.moved()
        self.assertEqual(got["exports"], [])
        self.assertEqual(got["unread"], [("../inputs/margin/activity.csv",
                                          "not UTF-8 or UTF-16 text")])

    def test_a_frozen_copy_that_cannot_be_read_is_named(self):
        self.frozen("activity.csv", (_IB + "Notes,Data,Activité\n")
                    .encode("cp1252"))
        self.shared_file("activity.csv", _IB)
        self.assertEqual(self.moved()["unread"],
                         [("inputs/margin/activity.csv",
                           "not UTF-8 or UTF-16 text")])

    # TJ-0708 ------------------------------------------------------
    def test_a_sidecar_mapping_corrected_is_named(self):
        self.frozen("generic_trades.csv", _GEN)
        self.frozen("generic_trades.csv.toml", _MAP)
        self.shared_file("generic_trades.csv.toml", _MAP_FIXED)
        got = self.moved()
        self.assertEqual(got["mappings"],
                         ["../inputs/margin/generic_trades.csv.toml"])
        self.assertEqual((got["exports"], got["unread"]), ([], []))

    def test_a_comment_or_the_layout_is_no_change(self):
        self.frozen("generic_trades.csv", _GEN)
        self.frozen("generic_trades.csv.toml", _MAP)
        self.shared_file("generic_trades.csv.toml",
                         "# made-up note\n" + _MAP.replace(" = ", "="))
        self.assertEqual(self.moved()["mappings"], [])

    def test_a_mapping_of_later_years_only_is_no_change(self):
        """A CSV with no row of 2024: its mapping reads no row of the
        filed year."""
        self.frozen("generic_2025.csv", _GEN.split("\n", 1)[0] + "\n"
                    + _ROW_25)
        self.frozen("generic_2025.csv.toml", _MAP)
        self.shared_file("generic_2025.csv.toml", _MAP_FIXED)
        self.assertEqual(self.moved()["mappings"], [])

    def test_the_shared_generic_toml_corrected_is_named_once(self):
        self.frozen("generic_a.csv", _GEN)
        self.frozen("generic_b.csv", _GEN.replace("ZZG", "ZZK"))
        self.frozen("generic.toml", _MAP)
        self.shared_file("generic.toml", _MAP_FIXED)
        self.assertEqual(self.moved()["mappings"],
                         ["../inputs/margin/generic.toml"])

    def test_a_sidecar_new_since_the_close_is_named(self):
        """The CSV was read with generic.toml; a sidecar of its own put
        in the shared folder reads it now."""
        self.frozen("generic_trades.csv", _GEN)
        self.frozen("generic.toml", _MAP)
        self.shared_file("generic_trades.csv.toml", _MAP_FIXED)
        self.assertEqual(self.moved()["mappings"],
                         ["../inputs/margin/generic_trades.csv.toml"])
        # The same mapping under the new name: nothing changed.
        self.shared_file("generic_trades.csv.toml", _MAP)
        self.assertEqual(self.moved()["mappings"], [])

    def test_a_mapping_removed_is_not_judged(self):
        self.frozen("generic_trades.csv", _GEN)
        self.frozen("generic_trades.csv.toml", _MAP)
        (self.shared / "margin" / "generic_trades.csv.toml").unlink()
        self.assertEqual(self.moved(),
                         {"exports": [], "mappings": [], "unread": []})

    def test_a_mapping_that_is_not_toml_is_named(self):
        self.frozen("generic_trades.csv", _GEN)
        self.frozen("generic_trades.csv.toml", _MAP)
        self.shared_file("generic_trades.csv.toml", _MAP + "[columns\n")
        got = self.moved()
        self.assertEqual(got["mappings"], [])
        self.assertEqual([p for p, _why in got["unread"]],
                         ["../inputs/margin/generic_trades.csv.toml"])
        self.assertTrue(got["unread"][0][1].startswith("not TOML"),
                        got["unread"])

    def test_a_broker_export_has_no_mapping(self):
        self.frozen("activity.csv", _IB)
        self.shared_file("generic.toml", _MAP)
        self.assertEqual(self.moved(),
                         {"exports": [], "mappings": [], "unread": []})


# ------------------------------------------------ the commands, end to end
_CA24 = ('[settings]\nlocal_timezone = "America/Toronto"\nyear = 2024\n'
         'country = "canada"\nbase_currency = "CAD"\n'
         'source_currencies = []\n\n'
         '[accounts.margin]\ntype = "taxable"\n\n'
         '[accounts.cash]\ntype = "taxable"\n')


class TestFiledYearSharedReview(unittest.TestCase):
    """A 2024 year folder reading ../inputs (a UTF-16 IB export in
    margin, a generic CSV and its mapping in cash), run and closed with
    its inputs frozen; each test works on a copy."""

    @classmethod
    def setUpClass(cls):
        cls._td = tempfile.TemporaryDirectory()
        top = Path(cls._td.name) / "p"
        year = top / "2024"
        year.mkdir(parents=True)
        cfg = year / "taxjson.toml"
        cfg.write_text(PL.set_key_text(_CA24, ("settings", "inputs_dir"),
                                       "../inputs"))
        (year / "ticker.map").write_text("")
        (top / "inputs" / "margin").mkdir(parents=True)
        (top / "inputs" / "cash").mkdir(parents=True)
        (top / "inputs" / "margin" / "activity.csv").write_text(
            _IB, encoding="utf-16")
        (top / "inputs" / "cash" / "generic_trades.csv").write_text(_GEN)
        (top / "inputs" / "cash" / "generic_trades.csv.toml").write_text(
            _MAP)
        for args in (("run", "--no-input"), ("close-year", "--yes")):
            r = _cli(year, *args)
            assert r.returncode == 0, (args, r.stdout[-2000:],
                                       r.stderr[-2000:])
        cls.base = top

    @classmethod
    def tearDownClass(cls):
        cls._td.cleanup()

    def copy_top(self):
        top = _tmp(self) / "p"
        shutil.copytree(self.base, top, symlinks=True)
        return top

    def years(self, top):
        r = _cli(top, "years", "--json")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        return json.loads(r.stdout)["years"][0]

    def check_filed(self, top):
        r = _cli(top / "2024", "check-filed")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("filed 2024: OK", r.stdout)
        return _said(r)

    def test_as_closed_nothing_is_named(self):
        top = self.copy_top()
        said = self.check_filed(top)
        self.assertNotIn("shared", said)
        self.assertNotIn("could not be compared", said)
        y = self.years(top)
        self.assertEqual((y["shared_exports_changed"],
                          y["shared_not_compared"]), ([], []))

    def test_a_utf16_sale_corrected_is_named(self):
        """TJ-0707: the 50 gain correction in the UTF-16 export."""
        top = self.copy_top()
        (top / "inputs" / "margin" / "activity.csv").write_text(
            _IB.replace(_IB_SALE, _IB_SALE_FIXED), encoding="utf-16")
        self.assertIn("note: filed 2024: 1 shared export(s) gained, changed "
                      "or lost a row of 2024 or earlier since the year was "
                      "closed: ../inputs/margin/activity.csv",
                      self.check_filed(top))
        y = self.years(top)
        self.assertEqual(y["shared_exports_changed"],
                         ["inputs/margin/activity.csv"])
        table = _cli(top, "years").stdout
        self.assertIn("shared changed", table)

    def test_a_mapping_corrected_is_named(self):
        """TJ-0708: the CSV is as closed; its mapping now reads the
        corrected amount column."""
        top = self.copy_top()
        (top / "inputs" / "cash" / "generic_trades.csv.toml").write_text(
            _MAP_FIXED)
        self.assertIn("note: filed 2024: 1 shared generic mapping(s) "
                      "changed since the year was closed: "
                      "../inputs/cash/generic_trades.csv.toml — it reads a "
                      "CSV with rows of 2024 or earlier otherwise than the "
                      "copy 2024 was closed with", self.check_filed(top))
        y = self.years(top)
        self.assertEqual(y["shared_exports_changed"],
                         ["inputs/cash/generic_trades.csv.toml"])
        det = " ".join(_cli(top, "years", "--details").stdout.split())
        self.assertIn("1 shared file(s) gained, changed or lost a row of "
                      "2024 (or a decision on it, or a generic mapping that "
                      "reads it) since the close "
                      "(inputs/cash/generic_trades.csv.toml)", det)

    def test_an_unreadable_shared_export_is_named(self):
        """TJ-0707: a legacy-encoding re-save is not compared, and said
        so — by check-filed and by `years`."""
        top = self.copy_top()
        (top / "inputs" / "margin" / "activity.csv").write_bytes(
            (_IB + "Notes,Data,Activité\n").encode("cp1252"))
        self.assertIn("note: filed 2024: 1 file could not be compared with "
                      "the copy 2024 was closed with: "
                      "../inputs/margin/activity.csv (not UTF-8 or UTF-16 "
                      "text)", self.check_filed(top))
        y = self.years(top)
        self.assertEqual(y["shared_exports_changed"], [])
        self.assertEqual(y["shared_not_compared"],
                         [{"file": "inputs/margin/activity.csv",
                           "why": "not UTF-8 or UTF-16 text"}])
        # From the folder holding the years: the act line names `-C
        # 2024`, short enough to be whole.
        table = " ".join(_cli(None, "years", cwd=top).stdout.split())
        self.assertIn("shared unchecked", table)
        self.assertIn("! 2024: 1 shared file could not be compared with its "
                      "copy — tjs -C 2024 check-filed", table)


if __name__ == "__main__":
    unittest.main()
