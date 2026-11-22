"""Fix round 2026-10-29, group transfers: moves of your own between two
taxable accounts, and the views of them.

TJ-0269 (the worktree audit's TJ-0380): a US project paired its
          transfer rows twice. The arrival stage (lib/transfer_in.
          arrivals: the same quantity, then the closest date) decided
          which transfer-ins came from outside the books; the lot carry
          (_custody_pairing: the first in leg of the same quantity in
          file order) decided which account received the sender's lots.
          With two candidates they chose different partners: qa's lots
          went to qb, whose own arrival was booked at its book value as
          well (held twice), and qc, the arrival's real partner, sold
          shares it never held (a short). One pairing now: the run's
          own-move plan (own_move_plan, the arrival stage's call) is
          what the lot carry reads.

Every fixture is synthetic: invented tickers, amounts and account ids.

Formerly: test_fix_1029_transfers
"""
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from _support.transfer_cases import _qt, _write
from _style import CapturedWidth

from taxjson.lib.dispatch import python_module_argv
import _paths

REPO_ROOT = _paths.REPO
SRC = REPO_ROOT / "src"
_WIDTH = CapturedWidth()


def setUpModule():
    os.environ["TAXJSON_LOCAL_TZ"] = "America/Toronto"
    _WIDTH.start()


def tearDownModule():
    _WIDTH.stop()


def _cli(root: Path, *args: str) -> subprocess.CompletedProcess:
    env = dict(os.environ, PYTHONPATH=str(SRC), TAXJSON_OFFLINE="1",
               TAXJSON_WIDTH="0")
    return subprocess.run(
        python_module_argv("taxjson.bin.taxjson_run",
                           ["-C", str(root), *args]),
        capture_output=True, text=True, env=env, stdin=subprocess.DEVNULL,
        timeout=300)


class TestTransfersView(unittest.TestCase):
    """TJ-0276 / TJ-0389: `tjs transfers` in a US project listed one
    move of your own as four rows (the broker's out and in rows, and the
    books' two lot-move legs as `book` rows); Canada's legs were hidden
    since TJ-0093. The legs are hidden in both countries.

    Formerly: test_fix_1029_transfers.TestTransfersView"""

    def test_one_move_is_its_two_broker_rows(self):
        for country in ("usa", "canada"):
            with self.subTest(country=country), \
                    tempfile.TemporaryDirectory() as td:
                cur = "CAD" if country == "canada" else "USD"
                root = _write(Path(td) / "p", country, {
                    "qa": _qt("99900001", cur,
                              ("2024-01-10", "Buy", "ZZQ CORP", 100, 2.0,
                               -200.0, "Trades"),
                              ("2024-03-01", "TF6", "ZZQ CORP TRANSFER OUT",
                               -100, 0.0, 0.0, "Transfers")),
                    "qb": _qt("99900002", cur,
                              ("2024-03-02", "TF6", "ZZQ CORP TRANSFER IN",
                               100, 0.0, 0.0, "Transfers"))})
                r = _cli(root, "run", "--no-input")
                self.assertEqual(r.returncode, 0, r.stderr[-2000:])
                self.assertTrue((root / "work" / "qb_own_moves.json")
                                .is_file())
                j = json.loads(_cli(root, "transfers", "--json").stdout)
                self.assertEqual(j["count"], 2)
                self.assertEqual(
                    sorted((t["account"], t["where"], t["arrival"])
                           for t in j["transfers"]),
                    [("qa", "sidecar", "-"), ("qb", "sidecar", "own move")])


if __name__ == "__main__":
    unittest.main()
