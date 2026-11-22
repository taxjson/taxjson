"""Re-audit-2 fixes, filing-reports list (helper D): the run pipeline
(crypto-sends stage, raw pass phantoms, blended .sum diagnostics, run
state banner), `sum`/`estimate` account handling and flag guards,
instalment dates, and the estimate's Canadian-issuer test and wording.

All data is synthetic (fake account ids, invented tickers).

Formerly: test_fix_a2_filrep_d
"""
import contextlib
import io
import tempfile
import unittest
from pathlib import Path


class TestAbortedFirstRunBanner(unittest.TestCase):
    """A2-0658: a first run that aborted after writing work/ (no .sum in
    reports/) leaves books the report commands still read; the 'not the
    clean result' banner fires for them.

    Formerly: test_fix_a2_filrep_d.TestAbortedFirstRunBanner"""

    def test_partial_books_without_reports_are_flagged(self):
        from taxjson.cli import common as cli_common
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "work").mkdir()
            (root / "reports").mkdir()
            cfg = {"settings": {"year": 2025, "country": "canada"},
                   "accounts": {"margin": {"type": "taxable"}}}
            self.assertEqual(cli_common._run_state_problems(root, cfg), [])
            (root / "work" / "margin_gains.json").write_text(
                '{"transactions": [], "summary": {}}')
            probs = cli_common._run_state_problems(root, cfg)
            self.assertEqual(len(probs), 1, probs)
            self.assertIn("did not finish", probs[0])
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                cli_common._warn_run_state(root, cfg)
            self.assertIn("not the clean result", err.getvalue())


if __name__ == "__main__":
    unittest.main()
