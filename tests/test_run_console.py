"""`taxjson run`'s console (docs/output-style.md, The run's console).

Every non-blank line a person reads starts with `==> ` (a step),
`Info: `, `Warning: `, `Error: `, or continues the entry above it,
flush-left; one blank line after an entry of more than one line and
nowhere else (never at the end); no `ATTENTION:` word, no
`(content: ...)` detection detail. The captured text (work/*.diag, reports/) does not
depend on it: a run shown to a person and a run captured for a program
(TAXJSON_WIDTH=0) write the same bytes. Every fixture is synthetic."""
import contextlib
import io
import os
import re
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from _style import Project, assert_console, project

from taxjson.lib import out

STEP = re.compile(r"(?m)^==> (.*)$")


def _flat(text: str) -> str:
    return " ".join(text.split())


class _Width(unittest.TestCase):
    """Each test sets TAXJSON_WIDTH itself (the suite runs with 0)."""

    def setUp(self):
        p = mock.patch.dict(os.environ)
        p.start()
        self.addCleanup(p.stop)
        os.environ.pop("TAXJSON_WIDTH", None)


class _Runs(unittest.TestCase):
    _cache = {}

    @classmethod
    def _copy(cls, country, pending=False):
        src = project(country, pending=pending)
        tmp = tempfile.mkdtemp(prefix="taxjson_run_console_")
        dst = Path(tmp) / country
        shutil.copytree(src.root, dst)
        return Project(dst, country), tmp

    @classmethod
    def run_of(cls, country, pending=False, **env):
        """(project, CompletedProcess) of a full `run --no-input` on a
        copy, once per class and key."""
        key = (country, pending, tuple(sorted(env.items())))
        if key not in cls._cache:
            p, tmp = cls._copy(country, pending)
            cls._cache[key] = (p, tmp, p.run("run", "--no-input", **env))
        return cls._cache[key][0], cls._cache[key][2]

    @classmethod
    def tearDownClass(cls):
        for _p, tmp, _r in cls._cache.values():
            shutil.rmtree(tmp, True)
        cls._cache.clear()


class TestEveryLine(_Runs):
    def _check(self, text, what):
        assert_console(self, text, allow=("taxjson elect ",))

    def test_full_run(self):
        for country in ("canada", "usa"):
            with self.subTest(country=country):
                _p, r = self.run_of(country)
                self.assertEqual(r.returncode, 0, r.stderr)
                self._check(r.stdout, "stdout")
                self._check(r.stderr, "stderr")

    def test_pending_run(self):
        for country in ("canada", "usa"):
            with self.subTest(country=country):
                _p, r = self.run_of(country, pending=True)
                self.assertEqual(r.returncode, 3, r.stderr)
                self._check(r.stdout, "stdout")
                self._check(r.stderr, "stderr")

    def test_blank_line_only_after_a_multi_line_message(self):
        _p, r = self.run_of("canada")
        self.assertNotIn("\n\n\n", r.stdout)
        self.assertFalse(r.stdout.startswith("\n"))
        self.assertFalse(r.stdout.endswith("\n\n"))
        lines = r.stdout.splitlines()
        blanks = [i for i, ln in enumerate(lines) if not ln]
        self.assertTrue(blanks, r.stdout)
        for i in blanks:
            # The line above a blank continues a message (flush-left),
            # the line below starts a new entry.
            self.assertFalse(out.CONSOLE_LINE_RE.match(lines[i - 1]),
                             lines[i - 1])
            self.assertFalse(lines[i - 1][:1].isspace(), lines[i - 1])
            self.assertTrue(out.CONSOLE_LINE_RE.match(lines[i + 1]),
                            lines[i + 1])
        # A one-line message or step is never followed by a blank line.
        for i, ln in enumerate(lines[:-1]):
            if out.CONSOLE_LINE_RE.match(ln) and not lines[i + 1]:
                self.fail(f"blank line after a one-line entry: {ln}")

    def test_merged_console(self):
        # stdout and stderr to one pipe (`2>&1`), as on a terminal: one
        # console, the rule holds across both streams.
        import subprocess
        from _style import env
        for country, pending in (("canada", False), ("usa", True)):
            with self.subTest(country=country, pending=pending):
                p, tmp = self._copy(country, pending)
                self.addCleanup(shutil.rmtree, tmp, True)
                r = subprocess.run(
                    [sys.executable, "-m", "taxjson.bin.taxjson_run", "-C",
                     str(p.root), "run", "--no-input"], cwd=p.root,
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    text=True, env=env(), stdin=subprocess.DEVNULL,
                    timeout=900)
                self.assertEqual(r.returncode, 3 if pending else 0,
                                 r.stdout)
                assert_console(self, r.stdout, allow=("taxjson elect ",))
                self.assertFalse(r.stdout.endswith("\n\n"))


class TestSteps(_Runs):
    def test_account_steps(self):
        p, r = self.run_of("canada")
        steps = STEP.findall(r.stdout)
        # Every equity account is read in the first pass (its transfer
        # journals join two listings for every account's books —
        # lib/cross_listings); its own steps follow later.
        f = steps.index("tfsa  (sheltered, first pass: transfers between "
                        "your accounts)")
        self.assertEqual(steps[f + 1], "Reading 1 file")
        i = steps.index("tfsa  (sheltered)")
        self.assertGreater(i, f)
        # One account's steps, in order, in the table's words.
        self.assertEqual(steps[i:i + 7], [
            "tfsa  (sheltered)",
            "Processing corporate actions",
            "Reading tfsa_extra.tt",
            "Sorting, de-duplicating, mapping tickers, converting currency",
            "Calculating capital gains",
            "Writing holdings reports/tfsa_holdings.toml",
            "Writing summary reports/tfsa.sum"])
        self.assertIn("Info: File inputs/tfsa/rbc_direct_demo.csv → "
                      "identified as RBC Direct Investing", r.stdout)
        self.assertIn("\nInfo: rbc_direct_demo.csv: 7 tax objects\n",
                      r.stdout)
        # Two brokers in one account name the broker per read.
        self.assertIn("Reading 1 Coinbase file", steps)
        self.assertIn("Reading 1 Kraken file", steps)
        self.assertIn("Loading currency rates", steps)
        self.assertIn("Combining sheltered accounts", steps)
        self.assertIn("Pooling cost and checking superficial losses across "
                      "taxable accounts (margin, qt)", steps)
        self.assertIn("Writing fees report reports/fees.rpt", steps)
        self.assertEqual(steps[-2:], [
            "Done. Reports are in reports/",
            "Before you trust these numbers (docs/getting-started.md, "
            "step 5)"])
        # The internal raw-holdings stages are not steps.
        self.assertNotRegex(r.stdout, r"(?m)^==> .*\braw\b")
        self.assertNotIn(str(p.root), r.stdout)

    def test_us_words(self):
        _p, r = self.run_of("usa")
        steps = STEP.findall(r.stdout)
        self.assertIn("Checking wash sales across taxable accounts "
                      "(margin)", steps)

    def test_closing_summary(self):
        _p, r = self.run_of("canada")
        tail = r.stdout[r.stdout.index("==> Before you trust"):]
        lines = tail.splitlines()
        self.assertTrue(lines[1].startswith(
            "Warning: 2 positions sold in 2024 with no purchase"), tail)
        self.assertEqual(lines[-1], "Info: Then run `taxjson checklist`. "
                                    "Every step is in "
                                    "docs/getting-started.md.")


class TestCaptured(_Runs):
    def test_detection_detail_stays_in_the_diag(self):
        p, r = self.run_of("canada")
        self.assertNotIn("(content:", r.stdout + r.stderr)
        diag = (p.root / "work" / "tfsa_detect.diag").read_text()
        self.assertIn("inputs/tfsa/rbc_direct_demo.csv → RBC Direct "
                      "Investing (content: ", diag)

    def test_attention_stays_in_the_diag(self):
        p, r = self.run_of("canada")
        self.assertNotIn("ATTENTION", r.stdout + r.stderr)
        diag = (p.root / "work" / "margin_ib.json.diag").read_text()
        self.assertIn("warning: ATTENTION: ib_demo.csv: the statement has "
                      "no Cash Report", diag)
        self.assertIn("Warning: ib_demo.csv: the statement has no Cash "
                      "Report", r.stdout)

    def test_captured_bytes_do_not_depend_on_the_console(self):
        # A run shown to a person (width 100) and a run captured for a
        # program (TAXJSON_WIDTH=0) write the same work/ diagnostics and
        # reports.
        a, _ra = self.run_of("canada")
        b, _rb = self.run_of("canada", TAXJSON_WIDTH="0")
        stamp = re.compile(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(:\d{2}"
                           r"(\.\d+)?)?([+-]\d{2}:?\d{2}|Z)?")

        def files(root):
            return sorted([p.relative_to(root) for p in
                           (root / "work").glob("*.diag")]
                          + [p.relative_to(root) for p in
                             (root / "reports").rglob("*") if p.is_file()])
        self.assertEqual(files(a.root), files(b.root))
        for f in files(a.root):
            with self.subTest(f=str(f)):
                x = stamp.sub("<T>", (a.root / f).read_text(errors="replace"))
                y = stamp.sub("<T>", (b.root / f).read_text(errors="replace"))
                self.assertEqual(x.replace(str(a.root), "<P>"),
                                 y.replace(str(b.root), "<P>"))


class TestHoldingsCheck(_Runs):
    def test_holdings_check_is_a_few_messages(self):
        p, tmp = self._copy("canada")
        self.addCleanup(shutil.rmtree, tmp, True)
        cfg = p.root / "taxjson.toml"
        cfg.write_text(cfg.read_text().replace(
            '[accounts.margin]\ntype = "taxable"\n',
            '[accounts.margin]\ntype = "taxable"\n'
            'holdings = ["holdings.toml"]\n'))
        r = p.run("run", "--no-input")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(out.console_lint(r.stdout), [], r.stdout)
        tail = r.stdout[r.stdout.index(
            "==> Checking positions against the broker's holdings files"):]
        body = tail[:tail.index("==> Before you trust")].splitlines()[1:]
        # Messages and their continuations only — the tables are
        # `taxjson sanity`'s.
        self.assertTrue(body and body[0].startswith(("Warning: ", "Info: ")),
                        tail)
        self.assertIn("`taxjson sanity`", _flat(" ".join(body)))
        self.assertNotIn("SANITY —", r.stdout)
        # Captured for a program (width 0): the sanity report as before.
        r0 = p.run("run", "--no-input", TAXJSON_WIDTH="0")
        self.assertIn("SANITY —", r0.stdout)


class TestFiledYears(_Runs):
    def test_filed_year_check_on_the_console(self):
        import json
        p, tmp = self._copy("canada")
        self.addCleanup(shutil.rmtree, tmp, True)
        c = p.run("close-year", "--force")
        self.assertEqual(c.returncode, 0, c.stderr)
        r = p.run("run", "--no-input")
        self.assertEqual(r.returncode, 0, r.stderr)
        assert_console(self, r.stdout)
        assert_console(self, r.stderr)
        self.assertIn("\n==> Checking the filed years\nInfo: Filed 2024: OK",
                      r.stdout)
        # Drifted: a Warning, its `- ` detail lines flush-left.
        lock = p.root / "filed" / "2024.json"
        doc = json.loads(lock.read_text())
        acct = sorted(doc["accounts"])[-1]
        doc["accounts"][acct]["realized"] += 100.0
        lock.write_text(json.dumps(doc))
        r = p.run("run", "--no-input")
        assert_console(self, r.stdout)
        assert_console(self, r.stderr)
        self.assertIn("Warning: filed 2024 DRIFTED vs 2024.json", r.stderr)


# ------------------------------------------------- the display text
class TestReword(_Width):
    def _shown(self, line):
        from taxjson.lib.stage_msg import console_lines
        return console_lines(line, "", width_=100, source=False)

    def _assert_shown(self, line, want):
        """The headline exactly; the details' words (they wrap at 100),
        every line flush-left."""
        got = self._shown(line)
        self.assertEqual(got[0], want[0])
        self.assertEqual(_flat(" ".join(got[1:])),
                         _flat(" ".join(want[1:])))
        self.assertTrue(all(x and not x[0].isspace() for x in got), got)
        self.assertTrue(all(len(x) <= 100 for x in got), got)

    def test_kraken_notes(self):
        cases = {
            "note: Kraken ledger kr_l.csv: 30 trade row(s) (15 trade(s)) "
            "are booked from the trades export beside it — every one "
            "matched.":
                ["Info: Kraken kr_l.csv: 30 ledger trade rows matched the "
                 "trades export"],
            "note: Kraken ledger kr_l.csv: ignored 16 fiat-cash or "
            "zero-amount row(s) (deposit x15, transfer x1) — moving your "
            "own cash to or from Kraken (a bank deposit, withdrawal or "
            "transfer), or a row that moves nothing, is not a tax event.":
                ["Info: Kraken kr_l.csv: ignored 16 fiat/zero rows (15 "
                 "deposits, 1 transfer) — not tax events"],
            "note: Kraken trades kr_t.csv: 1 fill(s) had the fee taken in "
            "the traded coin (per the ledger) — booked as fewer coins "
            "received / more coins given, with no quote-currency fee; "
            "those fees are therefore NOT in the fee reports (fees.rpt, "
            "fees-sum, the .sum FEES line).":
                ["Info: Kraken kr_t.csv: 1 fill paid the fee in the traded "
                 "coin",
                 "Booked as fewer coins received or more given; these "
                 "fees are not in the fee reports."],
            "note: kr_l.csv: 3 recognized non-event row(s) not translated "
            "— Kraken Earn wallet move (allocation/deallocation): 3.":
                ["Info: kr_l.csv: 3 rows skipped (not tax events)",
                 "Kraken Earn wallet move (allocation/deallocation): 3."],
            "  kr_l.csv: 2 TRANSFER row(s) kept aside (custody evidence, "
            "not tax events — view with `taxjson transfers`)":
                ["Info: kr_l.csv: 2 transfer rows kept aside (not tax "
                 "events; `taxjson transfers` lists them)"],
            "  kr_l.csv: 251 tax objects":
                ["Info: kr_l.csv: 251 tax objects"],
            "note: Kraken ledger refid DS*** (2026-10-01): dust sweep: "
            "AVAX 0.0000000003 (0.00 USD); SOL 0.0000000002 (0.00 USD) — 2 "
            "legs under the books' zero (1e-09 units) and worth at most "
            "0.01 USD, not booked: a disposition of a negligible amount; "
            "the coins stay in the holdings as a residue. The receipt is "
            "split over the other legs.":
                ["Info: Kraken: dust sweep DS*** (2026-10-01): 2 legs under "
                 "1e-09 units not booked",
                 "AVAX 0.0000000003 (0.00 USD); SOL 0.0000000002 (0.00 "
                 "USD): worth at most 0.01 USD. A disposition",
                 "of a negligible amount; the coins stay in the holdings "
                 "as a residue. The receipt is split over the",
                 "other legs."],
            "note: Kraken ledger refid XA*** (2026-03-12): instant trade: "
            "BTC 0.0000000006 received (0.00 USD) — a leg under the books' "
            "zero (1e-09 units) and worth at most 0.01 USD, not booked: an "
            "acquisition of a negligible amount; the coins are not added "
            "to the holdings.":
                ["Info: Kraken: instant trade XA*** (2026-03-12): 1 leg under "
                 "1e-09 units not booked",
                 "BTC 0.0000000006 received (0.00 USD): worth at most 0.01 "
                 "USD. An acquisition of a negligible",
                 "amount; the coins are not added to the holdings."],
        }
        for line, want in cases.items():
            with self.subTest(line=line[:40]):
                self._assert_shown(line, want)

    def test_crypto_sends_hint_keeps_the_action(self):
        for line, words in (
                ("  NOTE: 2 crypto withdrawal/send(s) among them — if any "
                 "left your ownership (gift or payment), each is a taxable "
                 "DISPOSITION at fair market value: `taxjson crypto-sends` "
                 "lists them with the fair value and writes the .tt sale "
                 "for each gift/payment (self-custody moves need nothing).",
                 "a gift or payment is a disposition at fair value"),
                ("  NOTE: 1 crypto withdrawal/send(s) among them — if any "
                 "paid for something (payment), each is a taxable SALE at "
                 "fair market value: `taxjson crypto-sends` lists them "
                 "with the fair value and writes the .tt sale for each "
                 "payment (a gift is not a sale for a US donor; it and "
                 "self-custody moves need nothing).",
                 "a payment is a sale at fair value")):
            with self.subTest(words=words):
                lines = self._shown(line)
                self.assertTrue(lines[0].startswith("Info: "), lines)
                self.assertIn(words, lines[0])
                self.assertIn("`taxjson crypto-sends` lists them",
                              _flat(" ".join(lines)))

    def test_unmatched_line_is_unchanged_but_labelled(self):
        self.assertEqual(self._shown("note: something else"),
                         ["Info: something else"])

    def test_captured_form_is_the_line(self):
        from taxjson.lib.stage_msg import console_lines
        line = "  kr_l.csv: 251 tax objects"
        self.assertEqual(console_lines(line, "", width_=0), [line])

    def test_attention_is_a_warning_with_its_topic(self):
        self.assertEqual(
            self._shown("warning: ATTENTION: short: OLDCO.US (margin)"),
            ["Warning: Short position: OLDCO.US (margin)"])
        self.assertEqual(
            self._shown("warning: ATTENTION: x.csv: no Cash Report"),
            ["Warning: x.csv: no Cash Report"])

    def test_continuations_are_flush_left(self):
        from taxjson.lib.stage_msg import is_continuation
        for line in ("  - an item", "      deeply indented detail",
                     "\tA tab-indented detail"):
            with self.subTest(line=line):
                self.assertEqual(self._shown(line), [line.strip()])
                self.assertTrue(is_continuation(line))
        long = "    " + "word " * 40
        lines = self._shown(long)
        self.assertGreater(len(lines), 1)
        self.assertTrue(all(re.match(r"\S", x) for x in lines), lines)
        # A parser's indented count line is a message of its own.
        self.assertFalse(is_continuation("  kr_l.csv: 251 tax objects"))
        self.assertFalse(is_continuation("warning: x"))

    def test_no_break_inside_parentheses(self):
        from taxjson.lib.stage_msg import split_message
        head, rest = split_message(
            "income year: X.TO: a distribution paid 2025-01-03 is income "
            "of 2024 (a Canadian trust's, s.104(13); the 2024 T3) — it is "
            "NOT in 2025's numbers.")
        self.assertTrue(head.endswith("(a Canadian trust's, s.104(13); "
                                      "the 2024 T3)"), head)
        self.assertEqual(rest, "It is NOT in 2025's numbers.")


class TestMessageDetails(_Width):
    def test_list_items_are_flush_left(self):
        lines = out.message("error", "2 problems", details=[
            "- " + "a long item " * 12, "- short"], width_=100)
        self.assertEqual(lines[0], "Error: 2 problems")
        self.assertEqual(len(lines), 4, lines)
        self.assertTrue(lines[1].startswith("- a long item"), lines)
        # The item's wrapped line: flush-left, no hanging indent.
        self.assertTrue(lines[2].startswith("long item"), lines)
        self.assertEqual(lines[3], "- short")
        # Captured (width 0): the bytes as they always were.
        cap = out.message("error", "2 problems", details=["- a", "- b"],
                          width_=0)
        self.assertEqual(cap, ["error: 2 problems", "  - a", "  - b"])

    def test_console_lint(self):
        for good in ("==> Step\nInfo: x\nmore\n\nWarning: y\nError: z\n",
                     "==> A long step\nwrapped\n\n==> Next\n",
                     "Error: x\n- item\nwrapped item\n- item 2",
                     "Info: one\nInfo: two\n==> step\n"):
            with self.subTest(good=good):
                self.assertEqual(out.console_lint(good), [])
        for bad in (
                "    deep\n", "plain\n", "\n",
                "Info: x\n  more\n",                 # indented
                "Info: x\n\nInfo: y\n",              # blank after one line
                "==> step\n\n==> step\n",
                "Info: x\nmore\nInfo: y\n",          # no blank after 2 lines
                "Info: x\nmore\n\n\nInfo: y\n",      # two blank lines
                "Info: x\nmore\n\n",                 # blank at the end
                "Info: x\nmore\n\nmore\n",           # a bare line
                "Warning: ATTENTION: x\n",
                "Info: a.csv → IB (content: x)\n", "note: x\n"):
            with self.subTest(bad=bad):
                self.assertTrue(out.console_lint(bad), bad)


class TestRunHelpers(_Width):
    def test_say_ignores_nesting_for_a_person(self):
        from taxjson.bin import taxjson_run as R
        err = io.StringIO()
        with contextlib.redirect_stderr(err), \
                mock.patch.object(out, "width", return_value=100):
            R._say("warning", "x: elections required", "Detail.",
                   indent="  ")
        self.assertEqual(err.getvalue(),
                         "Warning: x: elections required\nDetail.\n")

    def test_step(self):
        from taxjson.bin import taxjson_run as R
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf), \
                mock.patch.object(out, "width", return_value=40):
            R._step("Writing summary reports/a_very_long_account_name.sum")
        lines = buf.getvalue().splitlines()
        self.assertTrue(lines[0].startswith("==> Writing summary"))
        self.assertGreater(len(lines), 1)
        # A wrapped step continues flush-left.
        self.assertTrue(all(x and not x[0].isspace() for x in lines[1:]))


if __name__ == "__main__":
    unittest.main()
