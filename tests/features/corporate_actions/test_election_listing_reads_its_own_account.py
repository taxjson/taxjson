"""GitHub issues #45, #46, #47, #48, #50, #51, #52: init writing through
an inputs/ link to outside the project, an empty fetched holdings
snapshot, a snapshot's broker id next to a local alias, account names
differing only by case, the election listing of a longer-named sibling
account, and command chaining (a `-C DIR` named like a command; an
account or folder named like a command). Synthetic data only.

Formerly: test_fix_issues_cli"""
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from _style import CapturedWidth

_WIDTH = CapturedWidth()


def setUpModule():
    _WIDTH.start()


def tearDownModule():
    _WIDTH.stop()


def _tmp(test) -> Path:
    d = Path(tempfile.mkdtemp())
    test.addCleanup(shutil.rmtree, d, True)
    return d


class TestDefaultedEventsExactAccount(unittest.TestCase):
    """#50: the election listing of account `a` reads a's corp files
    only, never those of a longer-named sibling `a_more`.

    Formerly: test_fix_issues_cli.TestDefaultedEventsExactAccount"""

    def test_sibling_files_are_not_read(self):
        from taxjson.cli import elections as cli_elect
        from taxjson.lib.corp_actions import SHELTERED_DEFAULT
        work = _tmp(self)

        def corp(fname, eid):
            (work / fname).write_text(json.dumps({"transactions": [{
                "action": "BUYSELL", "quantity": 1, "date": "2024-06-01",
                "symbol": "QZZQ.TO", "corp_election": SHELTERED_DEFAULT,
                "corp_event_id": eid}]}))
        corp("a_more_ib_corp.json", "synthetic-child-event")
        corp("a_questrade_corp.json", "synthetic-own-event")
        self.assertEqual(sorted(cli_elect._defaulted_events(work, "a")),
                         ["synthetic-own-event"])
        self.assertEqual(sorted(cli_elect._defaulted_events(work, "a_more")),
                         ["synthetic-child-event"])



if __name__ == "__main__":
    unittest.main()
