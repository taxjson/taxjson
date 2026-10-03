"""Moved from the core's tests/test_fix_a2_errb.py with `taxjson fetch` (now the
taxjson-fetch plugin). Original module docstring:

Regression pins for the re-audit-2 error-handling lists errors-02 and
errors-05: one-line errors (never a traceback) and consistent exit codes
in `taxjson` (bin/taxjson_run.py), the console-script trampoline
(bin/_entry.py) and `taxjson watch`.

Exit-code rule (lib/cli_diag): 0 ok, 1 the command's finding (or a
refusal such as 'run `taxjson run` first'), 2 a named input or output
that cannot be read or written, 130 Ctrl-C, 141 a closed stdout pipe.

Synthetic projects only (account `m`, hand-written FX rates in work/,
TAXJSON_OFFLINE set): nothing here touches the network.
"""
# First: puts the plugin, the core and its test helpers on sys.path and
# registers the plugin's entry point when it is not pip-installed.
import _support  # noqa: F401
import os
import unittest
from pathlib import Path


REPO_ROOT = Path(os.environ.get("ERRB_REPO") or Path(__file__).resolve().parent.parent)


class TestFetchHelpers(unittest.TestCase):

    def test_merge_csv_with_bom(self):
        """A2-1445."""
        from taxjson_fetch.command import _merge_csv_text
        qt = "Transaction Date,Action,Symbol\n2025-01-02,Buy,ABC\n"
        merged, added = _merge_csv_text("﻿" + qt, qt)
        self.assertEqual(added, 0)

    def test_ib_flex_text_with_bom_is_decoded(self):
        """A2-0799: fetch decodes like detection (BOM dropped)."""
        from taxjson_fetch import api as F
        from taxjson.lib.brokerages.base import decode_broker_text
        text = ("Statement,Header,Field Name,Field Value\n"
                "Statement,Data,BrokerName,Interactive Brokers\n"
                "Trades,Header,DataDiscriminator,Asset Category\n")
        raw = b"\xef\xbb\xbf" + text.encode()
        self.assertTrue(F.looks_like_ib_statement(decode_broker_text(raw)))
        src = (_support.PLUGIN_SRC / "command.py").read_text()
        self.assertNotIn('text = raw.decode("utf-8", "replace")', src)


if __name__ == "__main__":
    unittest.main()
