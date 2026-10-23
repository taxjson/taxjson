"""`sum --json` and `form-export --json` carry one readiness record
(lib/readiness): `complete` and the open `issues` — the run state, the
sales left out of the totals, and the engine's conservation warnings
(the 2026-10 critique: plausible figures that leave something out must
say so where a reader of the JSON looks). Synthetic books only."""
import json
import tempfile
import unittest
from pathlib import Path

from _qa_project import project, tj
from test_workflow_audit_fixes import QT_HEAD, QT_ROWS

from taxjson.lib import readiness as RD


class TestBuild(unittest.TestCase):
    def test_complete_only_with_no_issue(self):
        self.assertEqual(RD.build(), {"complete": True, "issues": []})
        r = RD.build(run_state=["inputs changed since the last full run"],
                     unknown_cost=2, no_purchase=1,
                     conservation=["X.US pool is EMPTY but carries 5.00"])
        self.assertFalse(r["complete"])
        self.assertEqual([i["kind"] for i in r["issues"]],
                         ["run-state", "unknown-cost", "no-purchase",
                          "conservation"])
        self.assertEqual(r["issues"][1]["count"], 2)
        self.assertTrue(all(i.get("fix") for i in r["issues"]))

    def test_conservation_lines_beside_the_gains_files(self):
        with tempfile.TemporaryDirectory() as td:
            w = Path(td)
            (w / "m_gains.json").write_text("{}")
            (w / "m_gains_wash.json").write_text("{}")
            (w / "m_gains.json.diag").write_text(
                "note: x\nwarning: conservation: X.US pool is EMPTY\n")
            # A wash file reads its account's pre-wash diag; each once.
            self.assertEqual(RD.conservation_lines(
                [w / "m_gains_wash.json", w / "m_gains.json"]),
                ["X.US pool is EMPTY"])


def _readiness(root, *cmd):
    return json.loads(tj(root, *cmd, "--json").stdout)["readiness"]


class TestCli(unittest.TestCase):
    def test_clean_books_are_complete(self):
        with tempfile.TemporaryDirectory() as td:
            root = project(td, "p", {"margin/qt.csv": QT_ROWS})
            tj(root, "run", "--no-input")
            self.assertEqual(_readiness(root, "sum"),
                             {"complete": True, "issues": []})
            self.assertTrue(_readiness(root, "form-export")["complete"])

    def test_a_sale_with_no_purchase(self):
        rows = (QT_HEAD
                + "2024-03-01,2024-03-05,Sell,ZZQ,ZZQ CORP,-100,60.00,"
                  "6000.00,0,6000.00,USD,Trades,55500001,Margin\n")  # pii-ok
        with tempfile.TemporaryDirectory() as td:
            root = project(td, "p", {"margin/qt.csv": rows})
            tj(root, "run", "--no-input", check=False)
            r = _readiness(root, "sum")
            self.assertFalse(r["complete"])
            self.assertTrue({"unknown-cost", "no-purchase"}
                            & {i["kind"] for i in r["issues"]}, r)

    def test_inputs_changed_since_the_run(self):
        with tempfile.TemporaryDirectory() as td:
            root = project(td, "p", {"margin/qt.csv": QT_ROWS})
            tj(root, "run", "--no-input")
            f = root / "inputs" / "margin" / "qt.csv"
            f.write_text(QT_ROWS
                         + "2024-06-03,2024-06-05,Buy,ZZQ,ZZQ CORP,10,50.00,"
                           "-500.00,0,-500.00,USD,Trades,55500001,"
                           "Margin\n")                           # pii-ok
            for cmd in ("sum", "form-export"):
                r = _readiness(root, cmd)
                self.assertFalse(r["complete"], cmd)
                self.assertIn("run-state", {i["kind"] for i in r["issues"]})

    def test_money_the_us_engine_leaves_out(self):
        """The US twin of audit #101: rows under 1e-8 units carrying
        money are a conservation issue, not silent dust."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "us"
            (root / "inputs" / "margin").mkdir(parents=True)
            (root / "taxjson.toml").write_text(
                '[settings]\nlocal_timezone = "America/New_York"\n'
                'year = 2025\ncountry = "usa"\nbase_currency = "USD"\n'
                'source_currencies = []\n'
                '[accounts.margin]\ntype = "taxable"\n')
            (root / "inputs" / "margin" / "t.tt").write_text(
                "BUYSELL 2025-01-02 09:30:00 SAMP.US 0.000000001 USD "
                "100000000000 100 0\n"
                "BUYSELL 2025-04-01 09:30:00 SAMP.US -0.000000001 USD "
                "200000000000 200 0\n")
            tj(root, "run", "--no-input")
            r = _readiness(root, "sum")
            self.assertFalse(r["complete"])
            (issue,) = [i for i in r["issues"] if i["kind"] == "conservation"]
            self.assertIn("SAMP.US 300.00", issue["detail"])
            out = tj(root, "sum")
            self.assertIn("conservation problem", out.stdout + out.stderr)


if __name__ == "__main__":
    unittest.main()
