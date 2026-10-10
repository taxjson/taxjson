"""`taxjson run`'s console, essentials first (docs/output-style.md):
each message one line naming the command with its detail, the closing
block under `==> Before you trust these numbers` at most six lines, and
`run --details` bringing every detail line back. The captured text
(width 0: work/*.diag, a program reading the console) keeps its bytes.
Synthetic style projects only (tests/_style.py)."""
import shutil
import tempfile
import unittest
from pathlib import Path

from _style import CONCISE_WIDTH, Project, assert_console, project

from taxjson.lib import first_run as FR
from taxjson.lib import out


def _copy(country, pending=False):
    src = project(country, pending=pending)
    tmp = tempfile.mkdtemp(prefix="taxjson_concise_run_")
    dst = Path(tmp) / country
    shutil.copytree(src.root, dst)
    return Project(dst, country), tmp


class _Runs(unittest.TestCase):
    _cache = {}

    @classmethod
    def run_of(cls, country, *args, pending=False, width=CONCISE_WIDTH):
        key = (country, pending, args, width)
        if key not in cls._cache:
            p, tmp = _copy(country, pending)
            cls._cache[key] = (tmp, p.run("run", "--no-input", *args,
                                          TAXJSON_WIDTH=str(width)))
        return cls._cache[key][1]

    @classmethod
    def tearDownClass(cls):
        for tmp, _r in cls._cache.values():
            shutil.rmtree(tmp, True)
        cls._cache.clear()


def _messages(text):
    """(entries, continuation lines not under an Error) of a console."""
    entries, conts, in_error = [], [], False
    for ln in text.splitlines():
        if not ln.strip():
            continue
        if out.CONSOLE_LINE_RE.match(ln):
            in_error = ln.startswith("Error: ")
            if not ln.startswith("==> "):
                entries.append(ln)
        elif not in_error:
            conts.append(ln)
    return entries, conts


class TestOneLineMessages(_Runs):
    def test_every_message_is_one_line(self):
        for country, pending in (("canada", False), ("usa", False),
                                 ("canada", True), ("usa", True)):
            with self.subTest(country=country, pending=pending):
                r = self.run_of(country, pending=pending)
                self.assertEqual(r.returncode, 3 if pending else 0,
                                 r.stderr)
                for text in (r.stdout, r.stderr):
                    entries, conts = _messages(text)
                    self.assertEqual(conts, [], text)
                    for ln in entries:
                        self.assertLessEqual(len(ln), CONCISE_WIDTH, ln)
                    assert_console(self, text, width=CONCISE_WIDTH,
                                   allow=("taxjson elect ",))

    def test_headlines_name_the_command(self):
        r = self.run_of("canada")
        text = r.stdout + r.stderr
        for phrase in (
                "Warning: ib_demo.csv: no Cash Report, so its cash is not "
                "reconciled: add it to the export",
                "Warning: Transfer-in: margin: 1 with NO cost, kept out of "
                "the books (XYZQ.US) — `taxjson transfers`",
                "Warning: Short position: OLDCO.US (margin): its purchase "
                "is missing — `taxjson find-missing-history`",
                "(GHOSTQ.TO) — `taxjson find-missing-history`",
                "can be read by other users: `chmod -R go-rwx` it"):
            self.assertIn(phrase, text)
        # Which broker each file was read as: --details.
        self.assertNotIn("identified as", text)

    def test_closing_block_budget(self):
        for country in ("canada", "usa"):
            with self.subTest(country=country):
                r = self.run_of(country)
                tail = r.stdout[r.stdout.index("==> Before you trust"):]
                lines = tail.splitlines()[1:]
                self.assertLessEqual(len(lines), FR.CONCISE_BUDGET, tail)
                # Every warning class, one line each, its command named.
                flat = "\n".join(lines)
                self.assertIn("Warning: NOT in the totals: 2 sales with "
                                "no purchase", flat)
                self.assertIn("transfer-in kept out with no cost (XYZQ.US) "
                              "— `taxjson transfers`", flat)
                self.assertIn("not checked against the broker's holdings "
                              "— `taxjson sanity`", flat)
                self.assertTrue(lines[-1].startswith(
                    "Info: Then run `taxjson checklist`"), tail)
                self.assertIn("`taxjson run --details`", lines[-1])


class TestDetails(_Runs):
    def test_details_bring_the_detail_back(self):
        r = self.run_of("canada", "--details")
        self.assertEqual(r.returncode, 0, r.stderr)
        text = " ".join((r.stdout + r.stderr).split())
        for phrase in (
                "Parsed money is NOT reconciled against IB's own totals. "
                "Include the Cash Report section in the export",
                "Add the original purchase as a .tt BUYSELL line",
                "Info: File inputs/margin/ib_demo.csv → identified as "
                "Interactive Brokers",
                "2 positions sold in 2024 with no purchase in your files, "
                "not in missing_history.tt",
                "Tighten it once: chmod -R go-rwx",
                "Info: Then run `taxjson checklist`: it checks every step "
                "and names the next one."):
            self.assertIn(phrase, text)
        assert_console(self, r.stdout, width=CONCISE_WIDTH)

    def test_captured_console_is_unchanged(self):
        # A program reading the run (width 0) gets the captured text:
        # the long closing lines, the ATTENTION marker lines whole.
        r = self.run_of("canada", width=0)
        text = r.stdout + r.stderr
        self.assertIn("2 positions sold in 2024 with no purchase in your "
                      "files, not in missing_history.tt", text)
        self.assertNotIn("`taxjson run --details`", text)


class TestConciseItems(unittest.TestCase):
    """lib/first_run.concise_items / render_blocks(concise=True) on a
    synthetic summary with every finding."""
    DOC = {
        "year": 2024,
        "no_purchase": [{"symbol": "AAA", "account": "m"},
                        {"symbol": "BBB", "account": "m"},
                        {"symbol": "CCC", "account": "q"}],
        "no_purchase_in_sum": [
            {"symbol": "DDD", "account": "m",
             "booked": FR.BOOKED_SHORT_COVER},
            {"symbol": "EEE", "account": "m", "booked": "matched"}],
        "zero_cost_sold": [{"symbol": "FFF", "account": "m"}],
        "zero_cost_held": [],
        "zero_cost_declared": [{"symbol": "GGG", "account": "m",
                                "events": ["E1"]}],
        "transfer_in_no_cost": [{"symbol": "HHH", "account": "m"}],
        "unchecked_accounts": [{"account": "m", "positions": 3}],
        "income_not_held": [{"symbol": "III", "account": "m"}],
    }

    def test_every_warning_kept_and_budget(self):
        blocks = FR.render_blocks(self.DOC, concise=True, width_=100,
                                  details_hint=True)
        lines = [ln for b in blocks for ln in b]
        self.assertTrue(lines[0].startswith("==> Before you trust"))
        body = lines[1:]
        self.assertTrue(all(len(b) == 1 for b in blocks), blocks)
        warns = [ln for ln in body if ln.startswith("Warning: ")]
        # no purchase, short cover, $0 cost, transfer-in, income: all
        # five warnings stay, whatever the budget.
        self.assertEqual(len(warns), 5, body)
        self.assertIn("+1 more", warns[0])
        for ln in body:
            self.assertLessEqual(len(ln), 100, ln)
        # The long form is unchanged.
        long = FR.render_blocks(self.DOC, width_=0)
        self.assertIn("3 positions sold in 2024 with no purchase in your "
                      "files", " ".join(ln for b in long for ln in b))

    def test_info_findings_fold_into_one(self):
        doc = dict(self.DOC, no_purchase_in_sum=[], zero_cost_sold=[],
                   income_not_held=[])
        body = [ln for b in FR.render_blocks(doc, concise=True,
                                             width_=100)[1:] for ln in b]
        self.assertLessEqual(len(body), FR.CONCISE_BUDGET, body)
        self.assertTrue(any("$0 cost you declared" in ln for ln in body)
                        or any("more check(s)" in ln for ln in body), body)
        self.assertEqual(body[-1], "Info: Then run `taxjson checklist`: "
                                   "it checks every step and names the "
                                   "next one.")

    def test_clean_is_silent(self):
        self.assertEqual(FR.render_blocks({"year": 2024}, concise=True), [])

    def test_read_short_line_agrees_with_its_count(self):
        def line(n):
            doc = {"no_purchase_in_sum": [
                {"symbol": f"QZQ{i}.US", "booked": "matched"}
                for i in range(n)]}
            return [t for _, t in FR.concise_items(doc)
                    if "read short" in t][0]
        self.assertIn("1 position read short by the history check is in "
                      "the totals", line(1))
        self.assertIn("2 positions read short by the history check are "
                      "in the totals", line(2))


if __name__ == "__main__":
    unittest.main()
