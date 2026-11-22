"""Fix round 7 (wave B) of 2026-10-29, the handoff group: the year
handoff's straddler and double checks, the deleted prior-year lock, the
all-history selection recompute and the journals report (synthetic
data only).

Formerly: test_fix_1029_r7b_handoff"""
import json
import tempfile
import unittest
from pathlib import Path

from _qa_project import tj


def _project(root, year, accounts=("margin",), prior=None):
    root.mkdir(parents=True, exist_ok=True)
    head = ('[settings]\nlocal_timezone = "America/Toronto"\n'
            f'year = {year}\ncountry = "canada"\nbase_currency = "CAD"\n'
            'source_currencies = []\n')
    if prior:
        head += f'prior_year_record = "{prior}"\n'
    (root / "taxjson.toml").write_text(head + "".join(
        f'[accounts.{a}]\ntype = "taxable"\n' for a in accounts))
    (root / "ticker.map").write_text("")
    for a in accounts:
        (root / "inputs" / a).mkdir(parents=True, exist_ok=True)
    return root


class TestAllHistoryPooledCost(unittest.TestCase):
    """TJ-0677: a window outside the tax year keeps the ACB pooled across
    the taxable accounts, as the tax year's books do.

    Formerly: test_fix_1029_r7b_handoff.TestAllHistoryPooledCost"""

    def test_stats_all_equals_the_tax_year(self):
        with tempfile.TemporaryDirectory() as td:
            p = _project(Path(td) / "p", 2025, ("a", "b"))
            (p / "inputs/a/t.tt").write_text(
                "BUYSELL 2025-01-02 10:00:00 SYN.TO 100 CAD 1 100 0\n"
                "BUYSELL 2025-06-03 10:00:00 SYN.TO -100 CAD 3 300 0\n")
            (p / "inputs/b/t.tt").write_text(
                "BUYSELL 2025-02-03 10:00:00 SYN.TO 100 CAD 2 200 0\n")
            tj(p, "run", "--no-input")
            year = json.loads(tj(p, "stats", "2025", "--json").stdout)
            full = json.loads(tj(p, "stats", "all", "--json").stdout)
            net = [d["classes"]["long-shares"]["net_pl"]
                   for d in (year, full)]
            self.assertEqual(net, [150.0, 150.0])


if __name__ == "__main__":
    unittest.main()
