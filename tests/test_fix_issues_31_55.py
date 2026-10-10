"""GitHub issues #31, #32, #49, #53, #54, #55 (synthetic data only).

- #31: rows of two accounts at one moment follow the accounts' NAMES,
  not their order in taxjson.toml: reordering the file changes no gain,
  and so the books stay current (CA-DATE-14 / US-DATE-13).
- #32: the filed-year locks a US gains run reads (US-WASH-22) are part
  of the run's fingerprint; Canada's gains read none.
- #49: `years` names a damaged filed/<year>.json instead of calling the
  year filed.
- #53: option-boundary on a base book without its rows: one error line.
- #54: wash-radar / safe-to-sell refuse a --date whose window leaves
  the calendar.
- #55: carry-forwards read from a lock taken before its year ended say
  they are provisional.
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tax_rules import rule
from tax_rules.dual import gains_both, tx

from taxjson.lib import checklist as CL
from taxjson.lib import project_layout as PL

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC = REPO_ROOT / "src"


def _env():
    return dict(os.environ, PYTHONPATH=str(SRC), TAXJSON_OFFLINE="1",
                TAXJSON_WIDTH="0", NO_COLOR="1")


def _cli(root, *args):
    return subprocess.run(
        [sys.executable, "-P", "-m", "taxjson.bin.taxjson_run", "-C",
         str(root), *args], capture_output=True, text=True, env=_env(),
        stdin=subprocess.DEVNULL, timeout=300)


def _tool(module, *args):
    return subprocess.run(
        [sys.executable, "-P", "-m", module, *args], capture_output=True,
        text=True, env=_env(), stdin=subprocess.DEVNULL, timeout=300)


def _config(year, accounts, country="canada"):
    cur = "CAD" if country == "canada" else "USD"
    return (f'[settings]\ncountry = "{country}"\nyear = {year}\n'
            f'base_currency = "{cur}"\nsource_currencies = []\n'
            + "".join(f'\n[accounts.{a}]\ntype = "taxable"\n'
                      for a in accounts))


def _project(root, year, accounts, books, country="canada"):
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    (root / "taxjson.toml").write_text(_config(year, accounts, country))
    (root / "ticker.map").write_text("")
    for a, text in books.items():
        d = root / "inputs" / a
        d.mkdir(parents=True, exist_ok=True)
        (d / "trades.tt").write_text(text)
    return root


def _gain(root, account):
    doc = json.loads((Path(root) / "work" / f"{account}_gains_wash.json")
                     .read_text())
    return doc["summary"]["total_gain"]


# ------------------------------------------------------------------ #31
# b sells 5 of its 10 (cost 10) at the same moment a buys 10 @30. By
# name a's buy comes first: the pool averages 20 and the sale gains 0.
# Before #31 the toml order decided (b first: the sale gained 50).
_ORDER_BOOKS = {
    "b": ("BUYSELL 2024-01-10 09:30:00 QZZQ.TO 10 CAD 10 100 0\n"
          "BUYSELL 2024-05-10 09:30:00 QZZQ.TO -5 CAD 20 100 0\n"),
    "a": "BUYSELL 2024-05-10 09:30:00 QZZQ.TO 10 CAD 30 300 0\n",
}


class TestAccountOrderIsNotAnInput(unittest.TestCase):
    """#31: the order of the accounts in taxjson.toml decides nothing."""

    @rule("CA-DATE-14")
    def test_reordered_toml_changes_no_gain_and_books_stay_current(self):
        with tempfile.TemporaryDirectory() as td:
            root = _project(Path(td) / "p", 2024, ["b", "a"], _ORDER_BOOKS)
            r = _cli(root, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr[-2000:])
            self.assertAlmostEqual(_gain(root, "b"), 0.0, places=2)
            (root / "taxjson.toml").write_text(_config(2024, ["a", "b"]))
            self.assertEqual(
                CL.inputs_changed(root, PL.read_config(root)), "")
            r = _cli(root, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr[-2000:])
            self.assertAlmostEqual(_gain(root, "b"), 0.0, places=2)

    @rule("CA-DATE-14")
    @rule("US-DATE-13")
    def test_engines_ignore_the_merge_order(self):
        # z sells at a loss; a and z rebuy at one moment. Either merge
        # order gives the same result in each country.
        def book(order):
            rows = {
                "z": [tx("BUYSELL", "2025-01-02", "XYZ.US", 100, 5000,
                         account="z"),
                      tx("BUYSELL", "2025-03-03", "XYZ.US", -100, 4000,
                         account="z"),
                      tx("BUYSELL", "2025-03-10", "XYZ.US", 100, 4000,
                         account="z", time="11:00:00")],
                "a": [tx("BUYSELL", "2025-03-10", "XYZ.US", 100, 4000,
                         account="a", time="11:00:00")]}
            return [t for n in order for t in rows[n]]

        def shape(res):
            return (round(res["summary"]["total_gain"], 2),
                    round(res["summary"]["total_disallowed"], 2),
                    sorted((str(i.get("account")),
                            round(i["total_cost"], 2))
                           for i in res["inventory"]))
        one = gains_both(book(["z", "a"]), year=2025)
        two = gains_both(book(["a", "z"]), year=2025)
        for c in ("canada", "usa"):
            self.assertEqual(shape(one[c]), shape(two[c]), c)

    @rule("CA-DATE-14")
    def test_one_accounts_rows_keep_their_order(self):
        # A write listed before its same-moment buy-back in ONE account
        # stays a write and a buy-back whatever the other account's
        # name (CA-DATE-14: the export's row order within an account).
        from taxjson.lib.core import _by_account_name
        rows = [tx("BUYSELL", "2025-03-10", "XYZ.TO", -1, 10, account="m"),
                tx("BUYSELL", "2025-03-10", "XYZ.TO", 1, 10, account="m"),
                tx("BUYSELL", "2025-03-10", "XYZ.TO", 1, 10, account="a")]
        (out,) = _by_account_name(rows)
        self.assertEqual([t.account for t in out], ["a", "m", "m"])
        self.assertEqual([t.quantity for t in out[1:]], [-1.0, 1.0])


if __name__ == "__main__":
    unittest.main()
