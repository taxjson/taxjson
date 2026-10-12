"""GitHub issue #80: taxjson-generate-parser's privacy gate counted only
some of the redactor's findings (a standalone preamble name is an
identity ROW, which it skipped; review candidates were not counted at
all) and then sent the ORIGINAL sample. The provider call is mocked:
no network. Every name and id below is synthetic."""
import contextlib
import io
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from taxjson.bin import taxjson_generate_parser as G
from taxjson.bin import taxjson_redact as R

_TRADES = ("Date,Action,Symbol,Quantity,Amount\n"
           "2025-01-06,BUY,SYN,1,100\n")


def _main(d: Path, sample: str, *extra: str):
    """Run the real main() on `sample` with an empty denylist and a mocked
    _call_claude; (exit code, prompts sent, stderr)."""
    csv = d / "sample.csv"
    csv.write_text(sample, encoding="utf-8")
    sent = []

    def fake(system_text, user_text, model_id):
        sent.append(user_text)
        return "class SyntheticBrokerage:\n    pass\n"
    err = io.StringIO()
    argv = ["taxjson-generate-parser", str(csv), "-o", str(d / "draft.py"),
            *extra]
    with mock.patch("taxjson.bin.taxjson_redact.load_denylist",
                    return_value=[]), \
            mock.patch.object(G, "_call_claude", fake), \
            mock.patch.object(sys, "argv", argv), \
            contextlib.redirect_stderr(err), \
            contextlib.redirect_stdout(io.StringIO()):
        try:
            G.main()
            rc = 0
        except SystemExit as e:
            rc = e.code
    return rc, sent, err.getvalue()


class GenerateParserGate(unittest.TestCase):

    def test_standalone_preamble_name_is_refused(self):
        with tempfile.TemporaryDirectory() as d:
            rc, sent, err = _main(Path(d), "Sample Person\n" + _TRADES)
        self.assertEqual(rc, 1, err)
        self.assertEqual(sent, [])
        self.assertIn("personal data", err)
        self.assertNotIn("Sample Person", err)

    def test_review_candidate_is_refused(self):
        # A Description cell the redactor cannot classify (it looks like
        # a person's name): listed for review, not redacted — refused.
        sample = ("Date,Action,Symbol,Description,Quantity,Amount\n"
                  "2025-01-06,BUY,SYN,JANE SAMPLEPERSON,1,100\n")
        with tempfile.TemporaryDirectory() as d:
            rc, sent, err = _main(Path(d), sample)
        self.assertEqual(rc, 1, err)
        self.assertEqual(sent, [])
        self.assertIn("could not classify", err)
        self.assertIn("line 2", err)
        self.assertNotIn("SAMPLEPERSON", err)

    def test_clean_sample_sends_the_redactors_output(self):
        # What goes out is the redactor's copy of the sample, not the
        # file's text: here they are the same.
        with tempfile.TemporaryDirectory() as d:
            rc, sent, err = _main(Path(d), _TRADES)
        self.assertEqual(rc, 0, err)
        self.assertEqual(len(sent), 1)
        self.assertIn("2025-01-06,BUY,SYN,1,100", sent[0])

    def test_outbound_text_is_the_redacted_copy(self):
        # A change the redactor makes without counting it (simulated by a
        # redactor whose report is empty) is refused, never sent.
        real = R.redact_text

        def uncounted(text, *a, **k):
            out, _rep = real(text, *a, **k)
            return out.replace("SYN", "REDACTED"), R.Report()
        with tempfile.TemporaryDirectory() as d, \
                mock.patch.object(R, "redact_text", uncounted):
            rc, sent, err = _main(Path(d), _TRADES)
        self.assertEqual(rc, 1, err)
        self.assertEqual(sent, [])
        self.assertIn("line 2", err)

    def test_redacted_ib_sample_passes(self):
        sample = ("Statement,Header,Field Name,Field Value\n"
                  "Account Information,Header,Field Name,Field Value\n"
                  "Account Information,Data,Name,Pat Contributor\n"
                  "Account Information,Data,Account,U5550001\n"  # pii-ok
                  "Trades,Header,Symbol,Date/Time,Quantity\n"
                  "Trades,Data,MSFT,2024-02-12,50\n")
        clean, _ = R.redact_text(sample, [])
        with tempfile.TemporaryDirectory() as d:
            rc, sent, err = _main(Path(d), sample)
            self.assertEqual(rc, 1, err)
            self.assertEqual(sent, [])
            rc, sent, err = _main(Path(d), clean)
        self.assertEqual(rc, 0, err)
        self.assertEqual(len(sent), 1)
        self.assertNotIn("Pat Contributor", sent[0])
        self.assertNotIn("5550001", sent[0])

    def test_override_still_sends_with_a_warning(self):
        with tempfile.TemporaryDirectory() as d:
            rc, sent, err = _main(Path(d), "Sample Person\n" + _TRADES,
                                  "--allow-unredacted")
        self.assertEqual(rc, 0, err)
        self.assertEqual(len(sent), 1)
        self.assertIn("warning: sending a sample", err)


class ReportKindsAreComplete(unittest.TestCase):
    """Every attribute of the redactor's Report is either an identity
    kind (counted by Report.identity_counts, which the gate reads) or
    declared bookkeeping: a new counter that is in neither fails here
    instead of being silently skipped by the gate again."""

    def test_every_report_attribute_is_classified(self):
        attrs = set(vars(R.Report()))
        kinds = {k for k, _ in R.Report.IDENTITY_KINDS}
        self.assertEqual(attrs - kinds - set(R.Report.NOT_IDENTITY), set())
        self.assertEqual(kinds & set(R.Report.NOT_IDENTITY), set())
        self.assertEqual((kinds | set(R.Report.NOT_IDENTITY)) - attrs, set())

    def test_identity_counts_reports_rows_and_review(self):
        _out, rep = R.redact_text("Sample Person\n" + _TRADES, [])
        labels = [lab for _n, lab in rep.identity_counts()]
        self.assertTrue(any("identity row" in lab for lab in labels), labels)


if __name__ == "__main__":
    unittest.main()
