"""Webull on the parse template (step 5h (5)): what the cut kept of the
one parse's quiet and loud paths. Synthetic data only (examples/webull_demo.csv)."""
import io
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path

from taxjson.lib.brokerages.webull import WebullBrokerage
import _paths

REPO_ROOT = _paths.REPO
DEMO = REPO_ROOT / "examples" / "webull_demo.csv"


def _parse(text: str):
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "webull_x.csv"
        p.write_text(text, encoding="utf-8")
        par = WebullBrokerage()
        with redirect_stderr(io.StringIO()) as err:
            txs = par.parse_file(p)
    return txs, par, err.getvalue()


class TestWebullOnTheTemplate(unittest.TestCase):
    def test_a_clean_export_prints_nothing_and_counts_its_rows(self):
        txs, par, err = _parse(DEMO.read_text(encoding="utf-8"))
        self.assertEqual(err, "")
        self.assertEqual(par._skip_counts, {})
        self.assertEqual(par._rows_seen, len(txs))

    def test_a_trailing_blank_or_separator_line_is_no_row(self):
        txs, par, err = _parse(DEMO.read_text(encoding="utf-8") + " \n,,\n")
        self.assertEqual((err, par._skip_counts), ("", {}))
        self.assertEqual(par._rows_seen, len(txs))

    def test_a_file_with_no_trading_summary_header_keeps_no_row_count(self):
        # taxjson-brokerage then warns about the file as it always did,
        # instead of taking it for an export of a period with no activity.
        txs, par, _ = _parse("Statement\nNo header here\nmore,cells,here\n")
        self.assertEqual((txs, par._rows_seen), ([], None))


if __name__ == "__main__":
    unittest.main()
