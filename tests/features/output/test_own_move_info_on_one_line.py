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


class TestOwnMoveConsoleLine(unittest.TestCase):
    """TJ-0270: on the run's console the US `Own-account move` Info
    line wrapped onto a second line without `--details` (it was shown
    whole, wrapped, rather than as a message): one line now, its
    detail with `--details` (docs/output-style.md, The run's
    console).

    Formerly: test_fix_1029_transfers.TestOwnMoveConsoleLine"""

    def _project(self, td, in_date):
        return _write(Path(td) / "p", "usa", {
            "qa": _qt("99900001", "USD",
                      ("2024-01-10", "Buy", "ZZQ CORP", 100, 2.0, -200.0,
                       "Trades"),
                      ("2024-03-01", "TF6", "ZZQ CORP TRANSFER OUT", -100,
                       0.0, 0.0, "Transfers")),
            "qb": _qt("99900002", "USD",
                      (in_date, "TF6", "ZZQ CORP TRANSFER IN", 100, 0.0,
                       0.0, "Transfers"))})

    def test_one_line_without_details(self):
        from _style import assert_console
        for in_date in ("2024-03-02", "2024-03-16"):        # a slow move
            with self.subTest(in_date=in_date), \
                    tempfile.TemporaryDirectory() as td:
                root = self._project(td, in_date)
                env = dict(os.environ, PYTHONPATH=str(SRC),
                           TAXJSON_OFFLINE="1", TAXJSON_WIDTH="120")
                run = subprocess.run(
                    python_module_argv("taxjson.bin.taxjson_run",
                                       ["-C", str(root), "run",
                                        "--no-input"]),
                    capture_output=True, text=True, env=env,
                    stdin=subprocess.DEVNULL, timeout=300)
                self.assertEqual(run.returncode, 0, run.stderr[-2000:])
                lines = run.stdout.splitlines()
                at = [i for i, ln in enumerate(lines)
                      if ln.startswith("Info: Own-account move: ZZQ.US 100 "
                                       "qa -> qb")]
                self.assertEqual(len(at), 1, run.stdout)
                nxt = lines[at[0] + 1]
                self.assertTrue(nxt.startswith(("==> ", "Info: ",
                                                "Warning: ")), nxt)
                assert_console(self, run.stdout, width=120)
                detail = subprocess.run(
                    python_module_argv("taxjson.bin.taxjson_run",
                                       ["-C", str(root), "run",
                                        "--no-input", "--details"]),
                    capture_output=True, text=True, env=env,
                    stdin=subprocess.DEVNULL, timeout=300)
                self.assertIn("The lots' basis and purchase dates go to "
                              "qb.", detail.stdout)
                if in_date == "2024-03-16":
                    self.assertIn("NOTOWN 2024-03-16 ZZQ.US 100 qa "
                                  "2024-03-01", detail.stdout)


if __name__ == "__main__":
    unittest.main()
