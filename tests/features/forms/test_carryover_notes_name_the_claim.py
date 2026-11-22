"""Backlog group `handoff` — handoff, carryover, carryforward, the
estimate, amt and option-boundary: TJ-0014, TJ-0016, TJ-0052, TJ-0068,
TJ-0069, TJ-0078, TJ-0084, TJ-0085, TJ-0115, TJ-0144, TJ-0145,
TJ-0146. Synthetic data only.

Formerly: test_fix_backlog_handoff"""
import unittest
from _style import CapturedWidth

_WIDTH = CapturedWidth()


def setUpModule():
    _WIDTH.start()


def tearDownModule():
    _WIDTH.stop()


def _flat(text: str) -> str:
    return " ".join(text.split())


# ------------------------------------------------------------ TJ-0145
class TestCarryoverNotes(unittest.TestCase):
    """TJ-0145: inside a project the notes name [carryover] claimed;
    the standalone tool keeps --claimed FILE.

    Formerly: test_fix_backlog_handoff.TestCarryoverNotes"""

    LEDGER = {"country": "canada", "rows": [
        {"year": 2024, "net_gain": -100.0, "claimed_applied": 0.0,
         "carryforward_balance": 100.0, "available_to_apply": 0.0,
         "carryback_candidates": [], "carried_back": 0.0}],
        "final_carryforward": 100.0}

    def test_in_project_names_the_toml(self):
        from taxjson.bin.taxjson_carryover import render
        text = _flat(render(dict(self.LEDGER), "CAD", 2024, False,
                            in_project=True))
        self.assertNotIn("--claimed", text)
        self.assertIn("record it in taxjson.toml, [carryover] then "
                      "`claimed = { YEAR = AMOUNT }`", text)
        text = _flat(render(dict(self.LEDGER), "CAD", 2024, False))
        self.assertIn("--claimed FILE", text)


if __name__ == "__main__":
    unittest.main()
